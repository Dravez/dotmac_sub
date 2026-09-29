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
}

class FakeForm {
    constructor(valid = true) {
        this.dataset = {automationTarget: "sales.quote"};
        this.elements = [new FakeControl()];
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

const installRuntime = (form, fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({scripts: []}),
})) => {
    const document = {
        documentElement: {},
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
    };
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
