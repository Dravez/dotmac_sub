(function () {
    "use strict";

    var card = document.querySelector("[data-profile-location-card]");
    var catalogNode = document.getElementById("profile-location-catalog");
    if (!card || !catalogNode) return;

    var catalog;
    try {
        catalog = JSON.parse(catalogNode.textContent || "{}");
    } catch (_error) {
        return;
    }

    var countryInput = card.querySelector("#profile-country-search");
    var countryCodeInput = card.querySelector("[data-profile-country-code]");
    var countryList = card.querySelector("#profile-country-options");
    var regionInput = card.querySelector("#profile-region");
    var stateList = card.querySelector("#profile-state-options");
    var regionHelp = card.querySelector("#profile-region-help");
    var lgaInput = card.querySelector("#profile-lga");
    var lgaList = card.querySelector("#profile-lga-options");
    var lgaHelp = card.querySelector("#profile-lga-help");
    if (
        !countryInput || !countryCodeInput || !countryList || !regionInput ||
        !stateList || !regionHelp || !lgaInput || !lgaList || !lgaHelp
    ) return;

    var displayNames = typeof Intl.DisplayNames === "function"
        ? new Intl.DisplayNames(["en"], { type: "region" })
        : null;
    var countries = (catalog.country_codes || []).map(function (code) {
        return {
            code: code,
            label: displayNames ? (displayNames.of(code) || code) : code
        };
    }).sort(function (left, right) {
        return left.label.localeCompare(right.label);
    });
    var states = catalog.nigeria_states || [];
    var lgasByState = catalog.nigeria_lgas_by_state || {};
    var activeCountry = "";
    var activeState = "";

    function normalized(value) {
        return String(value || "").trim().toLocaleLowerCase();
    }

    function replaceOptions(list, values) {
        list.replaceChildren();
        values.forEach(function (value) {
            var option = document.createElement("option");
            option.value = value;
            list.appendChild(option);
        });
    }

    function countryForInput(value) {
        var key = normalized(value);
        return countries.find(function (country) {
            return normalized(country.label) === key || normalized(country.code) === key;
        }) || null;
    }

    function stateForInput(value) {
        var key = normalized(value);
        return states.find(function (state) {
            return normalized(state.label) === key || normalized(state.value) === key;
        }) || null;
    }

    function setLgaOptions(stateValue, preserveValue) {
        replaceOptions(lgaList, lgasByState[stateValue] || []);
        if (!preserveValue) lgaInput.value = "";
        lgaInput.disabled = !stateValue;
        lgaHelp.textContent = stateValue
            ? "Type to search LGAs in the selected state."
            : "Select a Nigerian state before selecting an LGA.";
    }

    function configureCountry(country, preserveValues) {
        var code = country ? country.code : "";
        countryCodeInput.value = code;
        countryInput.setCustomValidity(
            country || !countryInput.value.trim()
                ? ""
                : "Select a country from the available list."
        );

        if (code === "NG") {
            regionInput.setAttribute("list", "profile-state-options");
            replaceOptions(stateList, states.map(function (state) { return state.label; }));
            regionHelp.textContent = "Type to search all Nigerian states and the FCT.";
            var selectedState = stateForInput(regionInput.value);
            if (selectedState) {
                regionInput.value = selectedState.label;
                activeState = selectedState.value;
            } else {
                activeState = "";
            }
            setLgaOptions(activeState, preserveValues);
        } else {
            regionInput.removeAttribute("list");
            replaceOptions(stateList, []);
            regionHelp.textContent = "Enter the state or region for this country.";
            replaceOptions(lgaList, []);
            lgaInput.disabled = true;
            if (!preserveValues) lgaInput.value = "";
            lgaHelp.textContent = "LGA applies to Nigerian addresses.";
            activeState = "";
        }
        activeCountry = code;
    }

    replaceOptions(countryList, countries.map(function (country) { return country.label; }));

    var initialCountry = countries.find(function (country) {
        return country.code === countryCodeInput.value.trim().toUpperCase();
    }) || null;
    if (initialCountry) countryInput.value = initialCountry.label;
    configureCountry(initialCountry, true);

    countryInput.addEventListener("input", function () {
        var selected = countryForInput(countryInput.value);
        var nextCode = selected ? selected.code : "";
        var countryChanged = Boolean(activeCountry && activeCountry !== nextCode);
        if (countryChanged) {
            regionInput.value = "";
            lgaInput.value = "";
        }
        configureCountry(selected, !countryChanged);
    });

    regionInput.addEventListener("input", function () {
        if (countryCodeInput.value !== "NG") return;
        var selected = stateForInput(regionInput.value);
        var nextState = selected ? selected.value : "";
        var stateChanged = activeState !== nextState;
        activeState = nextState;
        setLgaOptions(activeState, !stateChanged);
    });

    var form = card.closest("form");
    if (form) {
        form.addEventListener("reset", function () {
            window.setTimeout(function () {
                var selected = countryForInput(countryInput.value);
                configureCountry(selected, true);
            }, 0);
        });
    }
})();
