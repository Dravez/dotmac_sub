const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const source = readFileSync(
    require.resolve("../../static/js/automation-client-runtime.js"),
    "utf8",
);

class FakeEvent {
    constructor(type) {
        this.type = type;
        this.defaultPrevented = false;
        this.immediatePropagationStopped = false;
    }

    preventDefault() {
        this.defaultPrevented = true;
    }

    stopImmediatePropagation() {
        this.immediatePropagationStopped = true;
    }
}

class FakeControl {
    constructor() {
        this.name = "lead_id";
        this.type = "text";
        this.disabled = false;
        this.value = "lead-1";
        this.dataset = {};
        this.validationMessage = "";
    }

    setCustomValidity(message) {
        this.validationMessage = message;
    }

    dispatchEvent() {}
}

class FakeForm {
    constructor(valid = true) {
        this.dataset = {automationTarget: "sales.quote"};
        this.elements = [new FakeControl()];
        this.elements.namedItem = (name) => this.elements.find((element) => element.name === name);
        this.noValidate = false;
        this.valid = valid;
        this.listeners = new Map();
        this.reportValidityCalls = 0;
        this.requestSubmitCalls = 0;
        this.bubbleSubmitCalls = 0;
    }

    addEventListener(type, listener, options = false) {
        const listeners = this.listeners.get(type) || [];
        listeners.push({capture: options === true || options.capture === true, listener});
        this.listeners.set(type, listeners);
    }

    dispatch(type, event) {
        for (const phase of [true, false]) {
            for (const entry of this.listeners.get(type) || []) {
                if (entry.capture !== phase) continue;
                entry.listener(event);
                if (event.immediatePropagationStopped) return;
            }
        }
    }

    checkValidity() {
        return this.valid && !this.elements[0].validationMessage;
    }

    reportValidity() {
        this.reportValidityCalls += 1;
        return this.checkValidity();
    }

    requestSubmit() {
        this.requestSubmitCalls += 1;
        this.dispatch("submit", new FakeEvent("submit"));
    }
}

class FakeFrame {
    constructor(context, responder) {
        this.context = context;
        this.responder = responder;
        this.listeners = new Map();
        this.attributes = new Map();
        this.contentWindow = {
            postMessage: (message) => {
                setImmediate(() => {
                    const data = this.responder(message);
                    this.context.windowMessage({source: this.contentWindow, data});
                });
            },
        };
    }

    setAttribute(name, value) {
        this.attributes.set(name, value);
    }

    addEventListener(type, listener) {
        this.listeners.set(type, listener);
    }

    set srcdoc(value) {
        void value;
        setImmediate(() => this.listeners.get("load")?.());
    }

    remove() {}
}

const installRuntime = (form, fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({scripts: []}),
}), sandboxResponder = () => ({
    type: "automation.result",
    requestId: "unused",
    ok: true,
    errors: [],
    sets: [],
    prevented: false,
})) => {
    let vmContext;
    const document = {
        documentElement: {},
        body: {
            append() {},
        },
        createElement() {
            const frame = new FakeFrame(vmContext, sandboxResponder);
            form.sandboxFrame = frame;
            return frame;
        },
        addEventListener(type, listener) {
            if (type === "DOMContentLoaded") this.ready = listener;
        },
        querySelectorAll() {
            return [form];
        },
    };
    const context = {
        console: {error() {}},
        document,
        Event: FakeEvent,
        HTMLElement: class {},
        MutationObserver: class {
            observe() {}
        },
        fetch: fetchImpl,
        crypto: require("node:crypto").webcrypto,
        TextEncoder,
        setTimeout,
        clearTimeout,
        addEventListener(type, listener) {
            if (type === "message") this.windowMessage = listener;
        },
    };
    vmContext = context;
    vm.runInNewContext(source, context);
    document.ready();
};

test("invalid forms never reach page submit handlers", async () => {
    const form = new FakeForm(false);
    form.addEventListener("submit", () => {
        form.bubbleSubmitCalls += 1;
    });
    installRuntime(form);

    form.dispatch("submit", new FakeEvent("submit"));
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(form.bubbleSubmitCalls, 0);
    assert.equal(form.requestSubmitCalls, 0);
    assert.equal(form.reportValidityCalls, 1);
});

test("published scripts run through the sandbox bridge and return typed form effects", async () => {
    const form = new FakeForm(true);
    form.addEventListener("submit", () => {
        form.bubbleSubmitCalls += 1;
    });
    const scriptSource = "api.error('sandbox blocked'); api.set('lead_id', 'updated'); api.preventDefault();";
    const digest = await require("node:crypto").webcrypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(scriptSource),
    );
    const hash = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    installRuntime(form, async () => ({
        ok: true,
        status: 200,
        json: async () => ({scripts: [{key: "test", source_code: scriptSource, content_sha256: hash}]}),
    }), (message) => {
        void message;
        return {
            type: "automation.result",
            requestId: message.requestId,
            ok: true,
            errors: message.context.eventName === "form.validate" ? ["sandbox blocked"] : [],
            sets: message.context.eventName === "form.validate" ? [{name: "lead_id", value: "updated"}] : [],
            prevented: message.context.eventName === "form.validate",
        };
    });
    await new Promise((resolve) => setImmediate(resolve));
    form.dispatch("submit", new FakeEvent("submit"));
    await new Promise((resolve) => setTimeout(resolve, 50));

    assert.equal(form.bubbleSubmitCalls, 0);
    assert.equal(form.requestSubmitCalls, 0);
    assert.equal(form.elements[0].value, "updated");
    assert.equal(form.elements[0].validationMessage, "sandbox blocked");
    assert.equal(form.sandboxFrame.attributes.get("sandbox"), "allow-scripts");
});

test("valid forms retry through the normal submit handler after automation", async () => {
    const form = new FakeForm(true);
    form.addEventListener("submit", () => {
        form.bubbleSubmitCalls += 1;
    });
    installRuntime(form);

    form.dispatch("submit", new FakeEvent("submit"));
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(form.requestSubmitCalls, 1);
    assert.equal(form.bubbleSubmitCalls, 1);
});

test("automation fetch failures report an actionable validation error", async () => {
    const form = new FakeForm(true);
    form.addEventListener("submit", () => {
        form.bubbleSubmitCalls += 1;
    });
    installRuntime(form, async () => {
        throw new Error("network unavailable");
    });

    form.dispatch("submit", new FakeEvent("submit"));
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(form.bubbleSubmitCalls, 0);
    assert.equal(form.requestSubmitCalls, 0);
    assert.equal(form.reportValidityCalls, 1);
    assert.equal(
        form.elements[0].validationMessage,
        "Client automation could not validate this form. Try again.",
    );
});
