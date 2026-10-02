"""
Rewrite raw source values into terms the concept matcher can resolve.

The matcher is good at "Greek" → Greek (1546483) but, being a text matcher, it
has nothing to go on for the shapes demographic columns usually arrive in:

  gender     "M", "f", "man", "Woman"      — abbreviations and synonyms
  race       "caucasian"                   — a synonym the vocabulary lacks
  ethnicity  "greece", "PRT", "GR"         — the country, not the nationality
             "non-hispanic"                — a negation a text matcher inverts

  geography  "GR", "USA"                   — country codes ("USA" alone is a village)
  visit      "ER", "ICU", "IP"             — abbreviations
  provider   "GP", "cardiologist"          — shorthand, the person for the field

So each value is normalized first, per domain. Gender has only two standard
concepts and is resolved here outright; for every other domain the rewritten
term is what gets sent to the matcher, restricted to the target domain, so the
vocabulary — not this table — decides whether e.g. "Chinese" is a Race or an
Ethnicity concept.
"""
from __future__ import annotations

import re

GENDER_MALE = 8507
GENDER_FEMALE = 8532

# Lower-cased, punctuation-stripped source value → standard gender concept.
# Deliberately no numeric codes: "1"/"2" mean male/female in one dataset and
# the reverse in the next, so guessing would silently swap patients' sex.
_GENDER_TERMS: dict[str, int] = {
    **{t: GENDER_MALE for t in (
        "m", "male", "males", "man", "men", "boy", "masc", "masculine", "masculino",
        "mr", "h", "hombre", "homme", "maennlich", "männlich", "mannlich",
        "αρρεν", "άρρεν", "ανδρας", "άνδρας", "α",
    )},
    **{t: GENDER_FEMALE for t in (
        "f", "female", "females", "woman", "women", "girl", "fem", "feminine", "femenino",
        "mrs", "ms", "miss", "w", "mujer", "femme", "weiblich",
        "θηλυ", "θήλυ", "γυναικα", "γυναίκα", "θ",
    )},
}

# Race values that name a standard concept by another word.
_RACE_SYNONYMS: dict[str, str] = {
    "caucasian": "White",
    "white caucasian": "White",
    "caucasian white": "White",
    "afro american": "African American",
    "afroamerican": "African American",
    "african american": "African American",
    "black african american": "Black or African American",
    "aa": "Black or African American",
    "hawaiian pacific islander": "Native Hawaiian or Other Pacific Islander",
    "american indian alaska native": "American Indian or Alaska Native",
}

# Ethnicity negations. A text matcher scores "non-hispanic" closest to
# "Hispanic or Latino" — the opposite concept — so these never reach it.
_ETHNICITY_SYNONYMS: dict[str, str] = {
    **{t: "Not Hispanic or Latino" for t in (
        "non hispanic", "nonhispanic", "not hispanic", "non hispanic or latino",
        "not hispanic or latino", "non latino", "not latino", "non hispanic latino",
        "not hispanic latino", "nh",
    )},
    **{t: "Hispanic or Latino" for t in (
        "hispanic", "latino", "latina", "latinx", "hispanic latino", "hispanic or latino",
    )},
}

# (nationality, country names, ISO 3166 alpha-2, alpha-3). Every nationality
# here is the exact name of a standard Race or Ethnicity concept in the Athena
# vocabulary; which of the two it is, the matcher's domain filter decides.
_COUNTRIES: list[tuple[str, tuple[str, ...], str, str]] = [
    ("Afghan", ("afghanistan",), "af", "afg"),
    ("Albanian", ("albania",), "al", "alb"),
    ("Algerian", ("algeria",), "dz", "dza"),
    ("American", ("united states", "united states of america", "usa", "america"), "us", "usa"),
    ("Andorran", ("andorra",), "ad", "and"),
    ("Angolan", ("angola",), "ao", "ago"),
    ("Argentinean", ("argentina",), "ar", "arg"),
    ("Armenian", ("armenia",), "am", "arm"),
    ("Australian", ("australia",), "au", "aus"),
    ("Austrian", ("austria",), "at", "aut"),
    ("Azerbaijani", ("azerbaijan",), "az", "aze"),
    ("Bahamian", ("bahamas", "the bahamas"), "bs", "bhs"),
    ("Bahraini", ("bahrain",), "bh", "bhr"),
    ("Bangladeshi", ("bangladesh",), "bd", "bgd"),
    ("Barbadian", ("barbados",), "bb", "brb"),
    ("Belarusian", ("belarus",), "by", "blr"),
    ("Belgian", ("belgium",), "be", "bel"),
    ("Belizean", ("belize",), "bz", "blz"),
    ("Beninese", ("benin",), "bj", "ben"),
    ("Bermudian", ("bermuda",), "bm", "bmu"),
    ("Bhutanese", ("bhutan",), "bt", "btn"),
    ("Bolivian", ("bolivia",), "bo", "bol"),
    ("Bosnian", ("bosnia", "bosnia and herzegovina"), "ba", "bih"),
    ("Botswanan", ("botswana",), "bw", "bwa"),
    ("Brazilian", ("brazil", "brasil"), "br", "bra"),
    ("British", ("united kingdom", "uk", "great britain", "britain"), "gb", "gbr"),
    ("Bruneian", ("brunei",), "bn", "brn"),
    ("Bulgarian", ("bulgaria",), "bg", "bgr"),
    ("Burkinabe", ("burkina faso",), "bf", "bfa"),
    ("Burmese", ("myanmar", "burma"), "mm", "mmr"),
    ("Burundi", ("burundi",), "bi", "bdi"),
    ("Cambodian", ("cambodia",), "kh", "khm"),
    ("Cameroonian", ("cameroon",), "cm", "cmr"),
    ("Canadian", ("canada",), "ca", "can"),
    ("Cape Verdean", ("cabo verde", "cape verde"), "cv", "cpv"),
    ("Central African Republic", ("central african republic",), "cf", "caf"),
    ("Chadian", ("chad",), "td", "tcd"),
    ("Chilean", ("chile",), "cl", "chl"),
    ("Chinese", ("china",), "cn", "chn"),
    ("Colombian", ("colombia",), "co", "col"),
    ("Comoran", ("comoros",), "km", "com"),
    ("Congolese", ("republic of the congo", "congo", "congo brazzaville"), "cg", "cog"),
    ("Congolese", ("democratic republic of the congo", "dr congo", "drc", "congo kinshasa"), "cd", "cod"),
    ("Costa Rican", ("costa rica",), "cr", "cri"),
    ("Croatian", ("croatia",), "hr", "hrv"),
    ("Cuban", ("cuba",), "cu", "cub"),
    ("Cypriot", ("cyprus",), "cy", "cyp"),
    ("Czech", ("czech republic", "czechia"), "cz", "cze"),
    ("Danish", ("denmark",), "dk", "dnk"),
    ("Djiboutian", ("djibouti",), "dj", "dji"),
    ("Dominican", ("dominican republic",), "do", "dom"),
    ("Dutch", ("netherlands", "the netherlands", "holland"), "nl", "nld"),
    ("East Timorese", ("east timor", "timor leste"), "tl", "tls"),
    ("Ecuadorian", ("ecuador",), "ec", "ecu"),
    ("Egyptian", ("egypt",), "eg", "egy"),
    ("Emirati", ("united arab emirates", "uae"), "ae", "are"),
    ("English", ("england",), "", ""),
    ("Equatorial Guinean", ("equatorial guinea",), "gq", "gnq"),
    ("Eritrean", ("eritrea",), "er", "eri"),
    ("Estonian", ("estonia",), "ee", "est"),
    ("Ethiopian", ("ethiopia",), "et", "eth"),
    ("Fijian", ("fiji",), "fj", "fji"),
    ("Finnish", ("finland",), "fi", "fin"),
    ("French", ("france",), "fr", "fra"),
    ("Gabonese", ("gabon",), "ga", "gab"),
    ("Gambian", ("gambia", "the gambia"), "gm", "gmb"),
    ("Georgian", ("georgia",), "ge", "geo"),
    ("German", ("germany", "deutschland"), "de", "deu"),
    ("Ghanaian", ("ghana",), "gh", "gha"),
    ("Greek", ("greece", "hellas", "ellada", "ελλαδα", "ελλάδα", "ελληνας", "έλληνας", "ελληνικη", "ελληνική"), "gr", "grc"),
    ("Grenadian", ("grenada",), "gd", "grd"),
    ("Guatemalan", ("guatemala",), "gt", "gtm"),
    ("Guinean", ("guinea",), "gn", "gin"),
    ("Bissau Guinean", ("guinea bissau",), "gw", "gnb"),
    ("Guyanese", ("guyana",), "gy", "guy"),
    ("Haitian", ("haiti",), "ht", "hti"),
    ("Honduran", ("honduras",), "hn", "hnd"),
    ("Hungarian", ("hungary",), "hu", "hun"),
    ("Icelandic", ("iceland",), "is", "isl"),
    ("Asian Indian", ("india",), "in", "ind"),
    ("Indonesian", ("indonesia",), "id", "idn"),
    ("Iranian", ("iran",), "ir", "irn"),
    ("Iraqi", ("iraq",), "iq", "irq"),
    ("Irish", ("ireland", "republic of ireland"), "ie", "irl"),
    ("Israeli", ("israel",), "il", "isr"),
    ("Italian", ("italy", "italia"), "it", "ita"),
    ("Ivory Coastian", ("ivory coast", "cote d ivoire", "côte d ivoire"), "ci", "civ"),
    ("Jamaican", ("jamaica",), "jm", "jam"),
    ("Japanese", ("japan",), "jp", "jpn"),
    ("Jordanian", ("jordan",), "jo", "jor"),
    ("Kenyan", ("kenya",), "ke", "ken"),
    ("Korean", ("south korea", "korea", "republic of korea"), "kr", "kor"),
    ("Kuwaiti", ("kuwait",), "kw", "kwt"),
    ("Laotian", ("laos",), "la", "lao"),
    ("Latvian", ("latvia",), "lv", "lva"),
    ("Lebanese", ("lebanon",), "lb", "lbn"),
    ("Lesotho", ("lesotho",), "ls", "lso"),
    ("Liberian", ("liberia",), "lr", "lbr"),
    ("Libyan", ("libya",), "ly", "lby"),
    ("Liechtensteiner", ("liechtenstein",), "li", "lie"),
    ("Lithuanian", ("lithuania",), "lt", "ltu"),
    ("Luxembourger", ("luxembourg",), "lu", "lux"),
    ("Macedonian", ("north macedonia", "macedonia"), "mk", "mkd"),
    ("Malagasy", ("madagascar",), "mg", "mdg"),
    ("Malawian", ("malawi",), "mw", "mwi"),
    ("Malaysian", ("malaysia",), "my", "mys"),
    ("Maldivian", ("maldives",), "mv", "mdv"),
    ("Malian", ("mali",), "ml", "mli"),
    ("Maltese", ("malta",), "mt", "mlt"),
    ("Marshallese", ("marshall islands",), "mh", "mhl"),
    ("Mauritanian", ("mauritania",), "mr", "mrt"),
    ("Mauritian", ("mauritius",), "mu", "mus"),
    ("Mexican", ("mexico",), "mx", "mex"),
    ("Moldovan", ("moldova",), "md", "mda"),
    ("Mongolian", ("mongolia",), "mn", "mng"),
    ("Montenegrin", ("montenegro",), "me", "mne"),
    ("Moroccan", ("morocco",), "ma", "mar"),
    ("Mozambican", ("mozambique",), "mz", "moz"),
    ("Namibian", ("namibia",), "na", "nam"),
    ("Nepalese", ("nepal",), "np", "npl"),
    ("Nicaraguan", ("nicaragua",), "ni", "nic"),
    ("Nigerian", ("nigeria",), "ng", "nga"),
    ("Northern Irish", ("northern ireland",), "", ""),
    ("Norwegian", ("norway",), "no", "nor"),
    ("Omani", ("oman",), "om", "omn"),
    ("Pakistani", ("pakistan",), "pk", "pak"),
    ("Palauan", ("palau",), "pw", "plw"),
    ("Palestinian", ("palestine",), "ps", "pse"),
    ("Paraguayan", ("paraguay",), "py", "pry"),
    ("Peruvian", ("peru",), "pe", "per"),
    ("Polish", ("poland",), "pl", "pol"),
    ("Portuguese", ("portugal",), "pt", "prt"),
    ("Puerto Rican", ("puerto rico",), "pr", "pri"),
    ("Qatari", ("qatar",), "qa", "qat"),
    ("Reunionese", ("reunion", "réunion"), "re", "reu"),
    ("Romanian", ("romania",), "ro", "rou"),
    ("Russian", ("russian federation", "russia"), "ru", "rus"),
    ("Rwandan", ("rwanda",), "rw", "rwa"),
    ("Salvadoran", ("el salvador",), "sv", "slv"),
    ("Samoan", ("samoa",), "ws", "wsm"),
    ("Sao Tomean", ("sao tome and principe", "sao tome"), "st", "stp"),
    ("Saudi", ("saudi arabia",), "sa", "sau"),
    ("Scottish", ("scotland",), "", ""),
    ("Senegalese", ("senegal",), "sn", "sen"),
    ("Serbian", ("serbia",), "rs", "srb"),
    ("Seychellois", ("seychelles",), "sc", "syc"),
    ("Sierra Leonean", ("sierra leone",), "sl", "sle"),
    ("Singaporean", ("singapore",), "sg", "sgp"),
    ("Slovak", ("slovakia",), "sk", "svk"),
    ("Slovene", ("slovenia",), "si", "svn"),
    ("Solomon Islander", ("solomon islands",), "sb", "slb"),
    ("Somali", ("somalia",), "so", "som"),
    ("South African", ("south africa",), "za", "zaf"),
    ("South Sudanese", ("south sudan",), "ss", "ssd"),
    ("Spaniard", ("spain", "espana", "españa"), "es", "esp"),
    ("Sri Lankan", ("sri lanka",), "lk", "lka"),
    ("Sudanese", ("sudan",), "sd", "sdn"),
    ("Surinamese", ("suriname",), "sr", "sur"),
    ("Swazi", ("eswatini", "swaziland"), "sz", "swz"),
    ("Swedish", ("sweden",), "se", "swe"),
    ("Swiss", ("switzerland",), "ch", "che"),
    ("Syrian", ("syria",), "sy", "syr"),
    ("Taiwanese", ("taiwan",), "tw", "twn"),
    ("Tanzanian", ("tanzania",), "tz", "tza"),
    ("Thai", ("thailand",), "th", "tha"),
    ("Togolese", ("togo",), "tg", "tgo"),
    ("Tongan", ("tonga",), "to", "ton"),
    ("Trinidadian", ("trinidad and tobago", "trinidad"), "tt", "tto"),
    ("Tunisian", ("tunisia",), "tn", "tun"),
    ("Ukrainian", ("ukraine",), "ua", "ukr"),
    ("Western Sahrawi", ("western sahara",), "eh", "esh"),
]


def _key(value: str) -> str:
    """Case-, accent-insensitive-enough lookup key: lower-case, punctuation and
    separators collapsed to single spaces ("Non-Hispanic" → "non hispanic")."""
    return re.sub(r"[\s\-_/.,;:()'’]+", " ", value.strip().lower()).strip()


def _country_lookup(include_alpha2: bool) -> dict[str, str]:
    table: dict[str, str] = {}
    for nationality, names, alpha2, alpha3 in _COUNTRIES:
        for name in names:
            table[_key(name)] = nationality
        if alpha3:
            table[alpha3] = nationality
        if alpha2 and include_alpha2:
            table[alpha2] = nationality
    return table


# Two-letter country codes collide with the codes race columns use ("AI" is
# Anguilla and American Indian), so only ethnicity — where a country code is
# the common case — reads them.
_RACE_COUNTRIES = _country_lookup(include_alpha2=False)
_ETHNICITY_COUNTRIES = _country_lookup(include_alpha2=True)


def _title(name: str) -> str:
    small = {"and", "of", "the"}
    return " ".join(w if w in small and i else w.capitalize() for i, w in enumerate(name.split()))


# Geography (the Location step's country columns): any name or ISO code of a
# country → the country's first-listed name, which is what both SNOMED
# ("Greece", "Russian Federation") and OSM ("Germany") call it — so the first
# name in _COUNTRIES is the vocabulary's spelling where the two differ. Matched raw, "USA" lands on the OSM village
# "Usa" and "PRT" on "Proyart".
_GEOGRAPHY_COUNTRIES: dict[str, str] = {}
for _nationality, _names, _alpha2, _alpha3 in _COUNTRIES:
    for _code in (*(_key(n) for n in _names), _alpha2, _alpha3):
        if _code:
            _GEOGRAPHY_COUNTRIES[_code] = _title(_names[0])
del _nationality, _names, _alpha2, _alpha3, _code

# Visit (visit concepts, place of service, admitted from / discharged to):
# abbreviations expanded to the words the concept names use. The vocabulary
# the concept should come from (Visit vs CMS Place of Service) is the caller's
# `prefer`, not decided here.
_VISIT_SYNONYMS: dict[str, str] = {
    **{t: "emergency room" for t in (
        "er", "ed", "a&e", "a e", "ae", "emergency", "emergency department", "emergency room",
        "emergency dept", "casualty",
    )},
    **{t: "inpatient" for t in (
        "ip", "inpatient", "in patient", "hospitalization", "hospitalisation", "admission",
        "admitted", "hospitalized", "hospitalised", "ward",
    )},
    **{t: "outpatient" for t in (
        "op", "outpatient", "out patient", "ambulatory", "clinic", "outpatient clinic", "opd",
    )},
    **{t: "intensive care" for t in ("icu", "itu", "intensive care unit", "micu", "sicu")},
    **{t: "telehealth" for t in (
        "telemedicine", "teleconsultation", "tele consultation", "video consultation",
        "phone", "telephone", "virtual",
    )},
    **{t: "home" for t in ("home", "home visit", "house call")},
}

# Provider specialties: common shorthand, and people named for their field
# ("cardiologist") rather than the field itself, which is how the specialty
# vocabularies name them ("Cardiology").
_PROVIDER_SYNONYMS: dict[str, str] = {
    "gp": "General Practice",
    "general practitioner": "General Practice",
    "pcp": "Family Practice",
    "family doctor": "Family Practice",
    "family physician": "Family Practice",
    "pediatrician": "Pediatric Medicine",
    "paediatrician": "Pediatric Medicine",
    "paediatrics": "Pediatric Medicine",
    "pediatrics": "Pediatric Medicine",
    "internist": "Internal Medicine",
    "surgeon": "General Surgery",
    "psychiatrist": "Psychiatry",
}


def gender_concept(value: str) -> int | None:
    """The standard gender concept a raw value unambiguously names, if any."""
    return _GENDER_TERMS.get(_key(value))


def normalize_term(value: str, domain: str) -> str:
    """The term to send to the matcher for `value` in `domain`.

    Unknown domains and unknown values pass through unchanged (trimmed), so
    this is safe to call for any domain.
    """
    key = _key(value)
    d = domain.lower()
    if d == "race":
        return _RACE_SYNONYMS.get(key) or _RACE_COUNTRIES.get(key) or value.strip()
    if d == "ethnicity":
        return _ETHNICITY_SYNONYMS.get(key) or _ETHNICITY_COUNTRIES.get(key) or value.strip()
    if d == "geography":
        return _GEOGRAPHY_COUNTRIES.get(key) or value.strip()
    if d == "visit":
        return _VISIT_SYNONYMS.get(key) or value.strip()
    if d == "provider":
        if key in _PROVIDER_SYNONYMS:
            return _PROVIDER_SYNONYMS[key]
        # "cardiologist" → "cardiology", "oncologists" → "oncology"
        return re.sub(r"ologists?$", "ology", value.strip(), flags=re.I)
    return value.strip()
