"""Typed option catalog and validation for customer contact addresses."""

from __future__ import annotations

from dataclasses import dataclass

from app.services import ncc_location

_ISO_ALPHA2_CODES = tuple(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI
    BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN
    CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK
    FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM
    HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN
    KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK
    ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP
    NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW
    SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF
    TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI
    VN VU WF WS YE YT ZA ZM ZW
    """.split()
)
_ISO_ALPHA2_CODE_SET = frozenset(_ISO_ALPHA2_CODES)
NIGERIA_COUNTRY_CODE = "NG"


@dataclass(frozen=True, slots=True)
class NigerianStateOption:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class CustomerProfileLocationCatalog:
    country_codes: tuple[str, ...]
    nigeria_states: tuple[NigerianStateOption, ...]
    nigeria_lgas_by_state: dict[str, tuple[str, ...]]

    def as_template_value(self) -> dict[str, object]:
        return {
            "country_codes": list(self.country_codes),
            "nigeria_states": [
                {"value": option.value, "label": option.label}
                for option in self.nigeria_states
            ],
            "nigeria_lgas_by_state": {
                state: list(lgas) for state, lgas in self.nigeria_lgas_by_state.items()
            },
        }


def canonical_country_code(value: object) -> str:
    """Return one admitted ISO 3166-1 alpha-2 code, or an empty string."""

    code = str(value or "").strip().upper()
    return code if code in _ISO_ALPHA2_CODE_SET else ""


def _state_label(state: str) -> str:
    if state == "FEDERAL CAPITAL TERRITORY":
        return "Federal Capital Territory (FCT) - Abuja"
    return state.title()


def location_catalog() -> CustomerProfileLocationCatalog:
    states = tuple(
        NigerianStateOption(value=state, label=_state_label(state))
        for state in ncc_location.states()
        if state != "INTERNATIONAL"
    )
    return CustomerProfileLocationCatalog(
        country_codes=_ISO_ALPHA2_CODES,
        nigeria_states=states,
        nigeria_lgas_by_state={
            option.value: ncc_location.lgas_for_state(option.value) for option in states
        },
    )
