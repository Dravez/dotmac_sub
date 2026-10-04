/* Governed Automation Center client-script adapter.
 *
 * Published source is executed in a sandboxed, opaque-origin iframe. The
 * parent page only exchanges JSON-like snapshots and typed results with that
 * frame; scripts never receive parent DOM nodes, a database/write client, or a
 * network-capable execution context.
 */
(() => {
    "use strict";

    const bundles = new Map();
    const forms = new WeakSet();
    const SANDBOX_TIMEOUT_MS = 5000;
    const SANDBOX_DOCUMENT = `<!doctype html>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; connect-src 'none'; form-action 'none'; base-uri 'none'; navigate-to 'none'">
<script>
(() => {
    "use strict";
    const send = (message) => parent.postMessage(message, "*");
    addEventListener("message", async (event) => {
        const request = event.data;
        if (!request || request.type !== "automation.execute") return;
        const errors = [];
        const sets = [];
        const currentValues = {...request.fields};
        let prevented = false;
        const addError = (message) => {
            if (errors.length >= 20) return;
            errors.push(String(message || "Client validation failed").slice(0, 1000));
        };
        const api = Object.freeze({
            get: (name) => currentValues[name],
            set: (name, value) => {
                if (!Object.prototype.hasOwnProperty.call(currentValues, name)) {
                    throw new Error("Unknown form field: " + name);
                }
                if (sets.length >= 100) throw new Error("Too many form updates");
                const normalized = value == null ? "" : String(value);
                currentValues[name] = normalized;
                sets.push({name, value: normalized});
            },
            error: addError,
            clearError: () => { errors.length = 0; },
            preventDefault: () => { prevented = true; },
        });
        try {
            const context = Object.freeze({
                targetType: request.context.targetType,
                recordId: request.context.recordId,
                eventName: request.context.eventName,
                fieldName: request.context.fieldName,
                fields: Object.freeze({...request.fields}),
            });
            const execute = new Function("context", "api", "\"use strict\";\n" + request.sourceCode + "\n"); // eslint-disable-line no-new-func
            await execute(context, api);
            send({
                type: "automation.result",
                requestId: request.requestId,
                ok: true,
                errors,
                sets,
                prevented,
            });
        } catch (error) {
            send({
                type: "automation.result",
                requestId: request.requestId,
                ok: false,
                error: String(error && error.message ? error.message : error).slice(0, 1000),
            });
        }
    });
})();
<\/script>`;

    let sandboxFrame = null;
    let sandboxReady = null;
    let requestSequence = 0;
    const pendingRequests = new Map();
    const runtimeWindow = typeof globalThis !== "undefined" ? globalThis : self;

    const sha256 = async (source) => {
        const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(source));
        return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    };

    const fieldsFor = (form) => Object.freeze(Object.fromEntries(
        [...form.elements]
            .filter((element) => element.name && !element.disabled)
            .map((element) => [element.name, element.value])
    ));

    const validationControlFor = (form) => [...form.elements].find((element) => (
        typeof element.setCustomValidity === "function"
        && !element.disabled
        && element.type !== "hidden"
    ));

    const clearValidationError = (form) => {
        const control = validationControlFor(form);
        if (!control?.dataset?.automationValidationError) return;
        control.setCustomValidity("");
        delete control.dataset.automationValidationError;
    };

    const setValidationError = (form, message) => {
        const control = validationControlFor(form);
        if (control) {
            control.setCustomValidity(message);
            control.dataset.automationValidationError = "1";
        }
        return control;
    };

    const rejectPendingRequests = (error) => {
        for (const pending of pendingRequests.values()) {
            clearTimeout(pending.timeout);
            pending.reject(error);
        }
        pendingRequests.clear();
    };

    runtimeWindow.addEventListener?.("message", (event) => {
        if (!sandboxFrame || event.source !== sandboxFrame.contentWindow) return;
        const message = event.data;
        if (!message || message.type !== "automation.result") return;
        const pending = pendingRequests.get(message.requestId);
        if (!pending) return;
        pendingRequests.delete(message.requestId);
        clearTimeout(pending.timeout);
        if (!message.ok) {
            pending.reject(new Error(message.error || "Sandboxed client script failed"));
            return;
        }
        pending.resolve(message);
    });

    const ensureSandbox = () => {
        if (sandboxReady) return sandboxReady;
        sandboxReady = new Promise((resolve, reject) => {
            sandboxFrame = document.createElement("iframe");
            sandboxFrame.setAttribute("sandbox", "allow-scripts");
            sandboxFrame.setAttribute("aria-hidden", "true");
            sandboxFrame.tabIndex = -1;
            sandboxFrame.hidden = true;
            sandboxFrame.addEventListener("load", () => resolve(sandboxFrame), {once: true});
            sandboxFrame.addEventListener("error", () => reject(new Error("Client script sandbox unavailable")), {once: true});
            sandboxFrame.srcdoc = SANDBOX_DOCUMENT;
            document.body.append(sandboxFrame);
        }).catch((error) => {
            rejectPendingRequests(error);
            sandboxReady = null;
            sandboxFrame?.remove();
            sandboxFrame = null;
            throw error;
        });
        return sandboxReady;
    };

    const executeInSandbox = async (sourceCode, context, fields) => {
        const frame = await ensureSandbox();
        const requestId = `${Date.now()}-${++requestSequence}`;
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                pendingRequests.delete(requestId);
                reject(new Error("Client script sandbox timed out"));
            }, SANDBOX_TIMEOUT_MS);
            pendingRequests.set(requestId, {resolve, reject, timeout});
            frame.contentWindow.postMessage({
                type: "automation.execute",
                requestId,
                sourceCode,
                context,
                fields,
            }, "*");
        });
    };

    const loadBundle = async (target, eventName) => {
        const key = `${target}:${eventName}`;
        if (!bundles.has(key)) {
            const request = fetch(`/admin/automation/client-scripts?target_type=${encodeURIComponent(target)}&event_name=${encodeURIComponent(eventName)}`, {
                credentials: "same-origin",
                headers: {Accept: "application/json"},
            }).then((response) => {
                if (!response.ok) throw new Error(`Client script bundle unavailable (${response.status})`);
                return response.json().then((body) => body.scripts || []);
            }).catch((error) => {
                bundles.delete(key);
                throw error;
            });
            bundles.set(key, request);
        }
        return bundles.get(key);
    };

    const apiError = (form, errors, message) => {
        errors.push(message);
        setValidationError(form, message);
    };

    const run = async (form, eventName, domEvent, fieldName) => {
        const scripts = await loadBundle(form.dataset.automationTarget, eventName);
        clearValidationError(form);
        if (!scripts.length) return true;
        const errors = [];
        const values = fieldsFor(form);
        const currentValues = {...values};
        const context = Object.freeze({
            targetType: form.dataset.automationTarget,
            recordId: form.dataset.automationRecordId || null,
            eventName,
            fieldName: fieldName || null,
            fields: values,
        });
        for (const script of scripts) {
            try {
                if (await sha256(script.source_code) !== script.content_sha256) {
                    throw new Error("Client script integrity check failed");
                }
                const result = await executeInSandbox(script.source_code, context, currentValues);
                for (const change of result.sets || []) {
                    const control = form.elements.namedItem(change.name);
                    if (!control || !("value" in control)) throw new Error(`Unknown form field: ${change.name}`);
                    control.value = change.value;
                    currentValues[change.name] = change.value;
                    control.dispatchEvent(new Event("input", {bubbles: true}));
                }
                for (const message of result.errors || []) {
                    const text = String(message || "Client validation failed");
                    errors.push(text);
                    setValidationError(form, text);
                }
                if (result.prevented) domEvent?.preventDefault();
            } catch (error) {
                console.error("Automation client script failed", script.key, error);
                if (eventName === "form.validate") apiError(form, errors, "A client automation validation failed.");
            }
        }
        return errors.length === 0;
    };

    const attach = (form) => {
        if (forms.has(form) || !form.dataset.automationTarget) return;
        forms.add(form);
        void run(form, "form.load", null, null).catch((error) => {
            console.error("Automation client form-load validation failed", error);
        });
        form.addEventListener("change", (event) => {
            const target = event.target;
            if (target instanceof HTMLElement && target.name) {
                void run(form, "field.change", event, target.name).catch((error) => {
                    console.error("Automation client field-change validation failed", error);
                });
            }
        });
        form.addEventListener("submit", (event) => {
            if (form.dataset.automationBypass === "1") {
                delete form.dataset.automationBypass;
                return;
            }
            if (event.defaultPrevented) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            void run(form, "form.validate", event, null).then((valid) => {
                const nativeValid = form.noValidate || form.checkValidity();
                if (valid && nativeValid) {
                    form.dataset.automationBypass = "1";
                    form.requestSubmit();
                } else if (!form.noValidate) {
                    form.reportValidity();
                }
            }).catch((error) => {
                console.error("Automation client form validation failed", error);
                setValidationError(form, "Client automation could not validate this form. Try again.");
                if (!form.noValidate) form.reportValidity();
            });
        }, true);
    };

    const scan = () => document.querySelectorAll("form[data-automation-target]").forEach(attach);
    document.addEventListener("DOMContentLoaded", scan);
    new MutationObserver(scan).observe(document.documentElement, {childList: true, subtree: true});
})();
