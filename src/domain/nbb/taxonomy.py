"""
NBB CBSO — Universal Taxonomy Label Map
Covers: m01 (verkort), m02 (volledig), m03 (micro/vereenvoudigd)
        Old pfs: format (2007–2020) + New bas:mXX format (2020+)

Structure per entry:
  "bas:mXX" or "PfsElementName" →
      (pcmn_code, dutch_label, section, schema_scope)

section codes:
  BS_A    = Balans Activa
  BS_L    = Balans Passiva
  IS      = Resultatenrekening
  IS_X    = Resultaatverwerking
  WORKERS = Personeel
  SOCIAL  = Sociaal verslag
  NOTES   = Toelichting
  META    = Metadata / identificatie
"""

# ─────────────────────────────────────────────────────────────────────────────
# ORDER for display (BS first, then IS, then derived, then notes)
# ─────────────────────────────────────────────────────────────────────────────
SECTION_ORDER  = ["BS_A", "BS_L", "IS", "IS_X", "WORKERS", "SOCIAL", "NOTES"]
SECTION_LABELS = {
    "BS_A":    "BALANS — ACTIVA",
    "BS_L":    "BALANS — PASSIVA & EIGEN VERMOGEN",
    "IS":      "RESULTATENREKENING",
    "IS_X":    "RESULTAATVERWERKING",
    "WORKERS": "PERSONEEL",
    "SOCIAL":  "SOCIAAL VERSLAG",
    "NOTES":   "TOELICHTING",
}

# ─────────────────────────────────────────────────────────────────────────────
# NEW FORMAT  bas:mXX → (PCMN code, Dutch label, section)
# Sources: NBB CBSO taxonomy label linkbases m01/m02/m03 (reconstructed)
# ─────────────────────────────────────────────────────────────────────────────
BAS_MAP = {
    # ── BALANS ACTIVA ──────────────────────────────────────────────────────
    "bas:m1":   ("20/28", "VASTE ACTIVA",                                  "BS_A"),
    "bas:m2":   ("22/27", "  Materiële vaste activa",                      "BS_A"),
    "bas:m3":   ("22",    "    Terreinen en gebouwen",                      "BS_A"),
    "bas:m4":   ("23",    "    Installaties, machines en uitrusting",       "BS_A"),
    "bas:m5":   ("24",    "    Meubilair en rollend materieel",             "BS_A"),
    "bas:m6":   ("26",    "    Leasing en soortgelijke rechten",            "BS_A"),
    "bas:m7":   ("27",    "    Overige materiële vaste activa",             "BS_A"),
    "bas:m8":   ("21",    "  Immateriële vaste activa",                    "BS_A"),
    "bas:m9":   ("28",    "  Financiële vaste activa",                     "BS_A"),
    "bas:m10":  ("280/1", "    Verbonden ondernemingen — deelnemingen",    "BS_A"),
    "bas:m11":  ("284/5", "    Andere ondernemingen — deelnemingen",       "BS_A"),
    "bas:m12":  ("29/58", "VLOTTENDE ACTIVA",                              "BS_A"),
    "bas:m13":  ("3",     "  Voorraden en bestellingen in uitvoering",     "BS_A"),
    "bas:m14":  ("30/36", "    Voorraden",                                 "BS_A"),
    "bas:m15":  ("37",    "    Bestellingen in uitvoering",                "BS_A"),
    "bas:m16":  ("40/41", "  Vorderingen op ten hoogste één jaar",         "BS_A"),
    "bas:m17":  ("40",    "    Handelsvorderingen",                        "BS_A"),
    "bas:m18":  ("41",    "    Overige vorderingen",                       "BS_A"),
    "bas:m19":  ("50/53", "  Liquide middelen",                            "BS_A"),
    "bas:m20":  ("490/1", "  Overlopende rekeningen (activa)",             "BS_A"),
    "bas:m21":  ("20/58", "TOTAAL DER ACTIVA",                             "BS_A"),

    # ── BALANS PASSIVA ─────────────────────────────────────────────────────
    "bas:m23":  ("10/15", "EIGEN VERMOGEN",                                "BS_L"),
    "bas:m24":  ("10",    "  Kapitaal",                                    "BS_L"),
    "bas:m25":  ("100",   "    Geplaatst kapitaal",                        "BS_L"),
    "bas:m26":  ("101",   "    Niet-opgevraagd kapitaal (–)",              "BS_L"),
    "bas:m27":  ("11",    "  Uitgiftepremies",                             "BS_L"),
    "bas:m28":  ("12",    "  Herwaarderingsmeerwaarden",                   "BS_L"),
    "bas:m29":  ("13",    "  Reserves",                                    "BS_L"),
    "bas:m30":  ("130",   "    Wettelijke reserve",                        "BS_L"),
    "bas:m31":  ("132",   "    Onbeschikbare reserves",                    "BS_L"),
    "bas:m32":  ("133",   "    Belastingvrije reserves",                   "BS_L"),
    "bas:m34":  ("134",   "    Beschikbare reserves",                      "BS_L"),
    "bas:m37":  ("14",    "  Overgedragen winst (verlies)",                "BS_L"),
    "bas:m38":  ("15",    "  Kapitaalsubsidies",                           "BS_L"),
    "bas:m39":  ("16",    "VOORZIENINGEN EN UITGESTELDE BELASTINGEN",      "BS_L"),
    "bas:m43":  ("17",    "Schulden op meer dan één jaar",                 "BS_L"),
    "bas:m44":  ("170/4", "  Financiële schulden > 1 jaar",               "BS_L"),
    "bas:m49":  ("175",   "  Handelsschulden > 1 jaar",                   "BS_L"),
    "bas:m50":  ("178/9", "  Overige schulden > 1 jaar",                  "BS_L"),
    "bas:m52":  ("42/48", "Schulden op ten hoogste één jaar",              "BS_L"),
    "bas:m53":  ("42/43", "  Financiële schulden ≤ 1 jaar",               "BS_L"),
    "bas:m54":  ("44",    "  Handelsschulden ≤ 1 jaar",                   "BS_L"),
    "bas:m55":  ("440/4", "    Leveranciers",                              "BS_L"),
    "bas:m56":  ("441",   "    Te betalen wissels",                        "BS_L"),
    "bas:m57":  ("45",    "  Schulden tov belasting/sociale lasten",       "BS_L"),
    "bas:m58":  ("46",    "  Ontvangen vooruitbetalingen",                 "BS_L"),
    "bas:m59":  ("47/48", "  Overige schulden ≤ 1 jaar",                  "BS_L"),
    "bas:m60":  ("492/3", "Overlopende rekeningen (passiva)",              "BS_L"),
    "bas:m61":  ("10/49", "TOTAAL DER PASSIVA",                           "BS_L"),

    # ── RESULTATENREKENING ─────────────────────────────────────────────────
    "bas:m68":  ("70/76A","BEDRIJFSOPBRENGSTEN",                           "IS"),
    "bas:m70":  ("70",    "  Omzet",                                       "IS"),
    "bas:m72":  ("71",    "  Wijziging in voorraden en bestellingen",      "IS"),
    "bas:m73":  ("72",    "  Geproduceerde vaste activa",                  "IS"),
    "bas:m77":  ("74",    "  Andere bedrijfsopbrengsten",                  "IS"),
    "bas:m79":  ("60/66A","BEDRIJFSKOSTEN",                               "IS"),
    "bas:m85":  ("60",    "  Aankopen van grond- en hulpstoffen / hdl.",   "IS"),
    "bas:m87":  ("61",    "  Diensten en diverse goederen",                "IS"),
    "bas:m101": ("62",    "  Bezoldigingen, sociale lasten en pensioenen", "IS"),
    "bas:m103": ("630",   "  Afschrijvingen en waardeverminderingen",      "IS"),
    "bas:m104": ("631/4", "  Waardeverminderingen op vlottende activa",    "IS"),
    "bas:m107": ("635/8", "  Voorzieningen voor risico's en kosten",       "IS"),
    "bas:m109": ("640/8", "  Andere bedrijfskosten",                       "IS"),
    "bas:m110": ("9901",  "BEDRIJFSRESULTAAT",                             "IS"),
    "bas:m111": ("75",    "  Financiële opbrengsten",                      "IS"),
    "bas:m115": ("65",    "  Financiële kosten",                           "IS"),
    "bas:m118": ("9903",  "WINST (VERLIES) VÓÓR BELASTINGEN",             "IS"),
    "bas:m120": ("67",    "  Belastingen op het resultaat",                "IS"),
    "bas:m122": ("9904",  "WINST (VERLIES) VAN HET BOEKJAAR",             "IS"),
    "bas:m124": ("9905",  "TE VERDELEN WINST (VERLIES)",                  "IS"),

    # ── RESULTAATVERWERKING ────────────────────────────────────────────────
    "bas:m130": ("694/6", "  Toe te voegen aan reserves",                  "IS_X"),
    "bas:m131": ("694",   "    Wettelijke reserve",                        "IS_X"),
    "bas:m132": ("696",   "    Andere reserves",                           "IS_X"),
    "bas:m133": ("690",   "  Over te dragen winst / verlies",              "IS_X"),
    "bas:m134": ("691",   "  Uit te keren dividend",                       "IS_X"),

    # ── PERSONEEL ──────────────────────────────────────────────────────────
    "bas:m140": ("9087",  "Gemiddeld aantal werknemers (VTE)",             "WORKERS"),
    "bas:m141": ("9088",  "Effectief aantal werknemers op afsluiting",     "WORKERS"),
    "bas:m142": ("1023",  "  Voltijds",                                    "WORKERS"),
    "bas:m143": ("1024",  "  Deeltijds",                                   "WORKERS"),
    "bas:m144": ("9086",  "Personeelskosten totaal",                       "WORKERS"),
}

# ─────────────────────────────────────────────────────────────────────────────
# Alternative/redundant bas: codes that appear in specific schema versions
# These often carry the same PCMN value but from a different dimension context
# ─────────────────────────────────────────────────────────────────────────────
BAS_ALIASES = {
    # m01 abbreviated uses different codes for some standard items
    "bas:m2":   "bas:m2",   # tangible fixed assets (abbreviated)
    "bas:m9":   "bas:m12",  # current assets in m01 maps to m12 in m02
}

# Codes from old schema that reuse same number:
# bas:m107 in m01 = personeel VTE (NOT the same as bas:m107 in m02 = voorzieningen!)
# We detect this by context (unit: pure vs EUR, am1 vs dec1)
BAS_M107_WORKERS_ONLY = True  # when unitRef = pure → personeel; EUR → voorzieningen


# ─────────────────────────────────────────────────────────────────────────────
# OLD FORMAT  pfs:ElementName → (PCMN code, Dutch label, section)
# Covers pfs-abbr-2007 through pfs-abbr-2020 (all abbreviated versions)
# Also covers pfs-full-* (full model old format)
# ─────────────────────────────────────────────────────────────────────────────
PFS_MAP = {
    # BALANS ACTIVA
    "FixedAssets":                              ("20/28", "VASTE ACTIVA",                              "BS_A"),
    "IntangibleFixedAssets":                    ("21",    "  Immateriële vaste activa",                "BS_A"),
    "TangibleFixedAssets":                      ("22/27", "  Materiële vaste activa",                  "BS_A"),
    "LandBuildings":                            ("22",    "    Terreinen en gebouwen",                  "BS_A"),
    "PlantMachineryEquipment":                  ("23",    "    Installaties, machines en uitrusting",   "BS_A"),
    "FurnitureVehicles":                        ("24",    "    Meubilair en rollend materieel",         "BS_A"),
    "LeasingRights":                            ("26",    "    Leasing en soortgelijke rechten",        "BS_A"),
    "OtherTangibleFixedAssets":                 ("27",    "    Overige materiële vaste activa",         "BS_A"),
    "FinancialFixedAssets":                     ("28",    "  Financiële vaste activa",                  "BS_A"),
    "ParticipatingInterests":                   ("280/1", "    Verbonden — deelnemingen",               "BS_A"),
    "OtherParticipatingInterests":              ("284/5", "    Andere — deelnemingen",                  "BS_A"),
    "CurrentsAssets":                           ("29/58", "VLOTTENDE ACTIVA",                          "BS_A"),
    "StocksContractsProgress":                  ("3",     "  Voorraden en bestellingen i.u.",           "BS_A"),
    "Stocks":                                   ("30/36", "    Voorraden",                              "BS_A"),
    "ContractsInProgress":                      ("37",    "    Bestellingen in uitvoering",             "BS_A"),
    "AmountsReceivableWithinOneYear":           ("40/41", "  Vorderingen ≤ 1 jaar",                    "BS_A"),
    "TradeDebtorsWithinOneYear":                ("40",    "    Handelsvorderingen",                     "BS_A"),
    "OtherAmountsReceivableWithinOneYear":      ("41",    "    Overige vorderingen",                    "BS_A"),
    "CashBankHand":                             ("50/53", "  Liquide middelen",                         "BS_A"),
    "DeferredChargesAccruedIncome":             ("490/1", "  Overlopende rekeningen (activa)",          "BS_A"),
    "Assets":                                   ("20/58", "TOTAAL DER ACTIVA",                         "BS_A"),

    # BALANS PASSIVA
    "EquityGroupCapital":                       ("10/15", "EIGEN VERMOGEN",                            "BS_L"),
    "CapitalSubscribedCapital":                 ("10",    "  Kapitaal",                                 "BS_L"),
    "IssuedCapital":                            ("100",   "    Geplaatst kapitaal",                     "BS_L"),
    "UncalledCapital":                          ("101",   "    Niet-opgevraagd kapitaal (–)",           "BS_L"),
    "SharePremiums":                            ("11",    "  Uitgiftepremies",                          "BS_L"),
    "RevaluationSurpluses":                     ("12",    "  Herwaarderingsmeerwaarden",                "BS_L"),
    "ReservesLegalReserve":                     ("13",    "  Reserves",                                 "BS_L"),
    "LegalReserve":                             ("130",   "    Wettelijke reserve",                     "BS_L"),
    "UnavailableReserves":                      ("132",   "    Onbeschikbare reserves",                 "BS_L"),
    "TaxFreeReserves":                          ("133",   "    Belastingvrije reserves",                "BS_L"),
    "AvailableReserves":                        ("134",   "    Beschikbare reserves",                   "BS_L"),
    "ProfitLossCarriedForward":                 ("14",    "  Overgedragen winst (verlies)",             "BS_L"),
    "InvestmentSubsidies":                      ("15",    "  Kapitaalsubsidies",                        "BS_L"),
    "Provisions":                               ("16",    "VOORZIENINGEN",                              "BS_L"),
    "AmountsPayableAfterOneYear":               ("17",    "Schulden > 1 jaar",                         "BS_L"),
    "FinancialDebtsAfterOneYear":               ("170/4", "  Financiële schulden > 1 jaar",            "BS_L"),
    "TradeDebtsAfterOneYear":                   ("175",   "  Handelsschulden > 1 jaar",                "BS_L"),
    "OtherAmountsPayableAfterOneYear":          ("178/9", "  Overige schulden > 1 jaar",               "BS_L"),
    "AmountsPayableWithinOneYear":              ("42/48", "Schulden ≤ 1 jaar",                         "BS_L"),
    "FinancialDebtWithinOneYear":               ("42/43", "  Financiële schulden ≤ 1 jaar",            "BS_L"),
    "TradeDebtsWithinOneYear":                  ("44",    "  Handelsschulden ≤ 1 jaar",                "BS_L"),
    "Suppliers":                                ("440/4", "    Leveranciers",                           "BS_L"),
    "BillsOfExchangePayable":                   ("441",   "    Te betalen wissels",                     "BS_L"),
    "TaxesSocialSecurity":                      ("45",    "  Belastingen en sociale lasten",            "BS_L"),
    "AdvancesReceivedOnContracts":              ("46",    "  Ontvangen vooruitbetalingen",              "BS_L"),
    "OtherAmountsPayableWithinOneYear":         ("47/48", "  Overige schulden ≤ 1 jaar",               "BS_L"),
    "AccruedChargesDeferredIncome":             ("492/3", "Overlopende rekeningen (passiva)",           "BS_L"),
    "EquityLiabilities":                        ("10/49", "TOTAAL DER PASSIVA",                        "BS_L"),

    # RESULTATENREKENING
    "OperatingIncome":                          ("70/76A","BEDRIJFSOPBRENGSTEN",                       "IS"),
    "Turnover":                                 ("70",    "  Omzet",                                    "IS"),
    "ChangeStocksWorkInProgress":               ("71",    "  Wijziging in voorraden en bestellingen",  "IS"),
    "OwnConstructionCapitalized":               ("72",    "  Geproduceerde vaste activa",               "IS"),
    "OtherOperatingIncome":                     ("74",    "  Andere bedrijfsopbrengsten",               "IS"),
    "OperatingCharges":                         ("60/66A","BEDRIJFSKOSTEN",                            "IS"),
    "ServicesGoodsMiscGoods":                   ("60/61", "  Aankopen hdl. goederen / diensten",       "IS"),
    "PurchasesGoods":                           ("60",    "    Aankopen grond- en hulpstoffen",         "IS"),
    "ServicesCosts":                            ("61",    "    Diensten en diverse goederen",           "IS"),
    "PersonnelCharges":                         ("62",    "  Bezoldigingen en sociale lasten",          "IS"),
    "DepreciationAmortisation":                 ("630",   "  Afschrijvingen en waardeverminderingen",   "IS"),
    "WriteDownsCurrentAssets":                  ("631/4", "  Waardeverminderingen vlottende activa",   "IS"),
    "ProvisionsRisksCharges":                   ("635/8", "  Voorzieningen voor risico's en kosten",   "IS"),
    "OtherOperatingCharges":                    ("640/8", "  Andere bedrijfskosten",                   "IS"),
    "OperatingProfitLoss":                      ("9901",  "BEDRIJFSRESULTAAT",                         "IS"),
    "FinancialIncome":                          ("75",    "  Financiële opbrengsten",                  "IS"),
    "FinancialCharges":                         ("65",    "  Financiële kosten",                       "IS"),
    "ProfitLossCurrentYear":                    ("9903",  "WINST (VERLIES) VÓÓR BELASTINGEN",         "IS"),
    "IncomeTaxes":                              ("67",    "  Belastingen op het resultaat",             "IS"),
    "ProfitLossCurrentYearAfterTax":            ("9904",  "WINST (VERLIES) VAN HET BOEKJAAR",         "IS"),
    "ProfitLossAvailableDistribution":          ("9905",  "TE VERDELEN WINST (VERLIES)",               "IS"),

    # PERSONEEL
    "AverageFTEWorkers":                        ("9087",  "Gemiddeld aantal werknemers (VTE)",         "WORKERS"),
    "AverageNumberWorkers":                     ("9086",  "  Personeelskosten totaal",                 "WORKERS"),
}

# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA DETECTION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def detect_schema(content: str) -> dict:
    """Identify format, model type, and version from XBRL content"""
    import re

    schema_url = ""
    hrefs = re.findall(r'href=["\']([^"\']+\.xsd)["\']', content[:4000])
    if hrefs:
        schema_url = hrefs[0]

    # Model type
    if "/mod/m01/" in schema_url or "abbreviated" in schema_url:
        model = "m01"
        model_label = "Verkort schema"
    elif "/mod/m02/" in schema_url or "full" in schema_url:
        model = "m02"
        model_label = "Volledig schema"
    elif "/mod/m03/" in schema_url or "micro" in schema_url or "simplified" in schema_url:
        model = "m03"
        model_label = "Micro/Vereenvoudigd schema"
    elif "/mod/m04/" in schema_url or "consolidated" in schema_url:
        model = "m04"
        model_label = "Geconsolideerd schema"
    else:
        # Detect from namespace
        if "cbso/dict/met" in content:
            model = "m01"
            model_label = "Nieuw schema (CBSO)"
        else:
            model = "m01"
            model_label = "Oud schema (PFS)"

    # Format: new (met:am1 / dim:bas) vs old (pfs: namespace)
    is_new = "met:am1" in content or "dim:bas" in content or "cbso/dict/met" in content

    # Version
    ver_match = re.search(r'/fws/([^/]+)/', schema_url)
    ver = ver_match.group(1) if ver_match else "legacy"

    return {
        "schema_url": schema_url,
        "model": model,
        "model_label": model_label,
        "format": "new" if is_new else "old",
        "version": ver,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DB MAPPINGS — taxonomy section codes → dim_pcmn_codes CHECK constraint values
# ─────────────────────────────────────────────────────────────────────────────
# Used by extractor.py when writing fact_financials_lines / dim_pcmn_codes lookups.
# CHECK in DB: section IN ('PL', 'BS_A', 'BS_L', 'WORKERS', 'PROFIT_APPR', 'NOTES')

SECTION_TO_DB = {
    "BS_A":    "BS_A",
    "BS_L":    "BS_L",
    "IS":      "PL",
    "IS_X":    "PROFIT_APPR",
    "WORKERS": "WORKERS",
    "SOCIAL":  "NOTES",      # SOCIAL is not in DB CHECK; collapse to NOTES
    "NOTES":   "NOTES",
    "META":    "NOTES",      # rare; safe fallback
}


def section_to_db(section: str) -> str:
    """Map taxonomy section code to DB-allowed section value."""
    return SECTION_TO_DB.get(section, "NOTES")


# ─────────────────────────────────────────────────────────────────────────────
# BE-GAAP-CI namespace mapping (third XBRL vocabulary, alongside bas: and pfs:)
# ─────────────────────────────────────────────────────────────────────────────
# Older/parallel Belgian XBRL taxonomy. Used by some NBB filings (post-2022 fallback,
# or specific corporate reports). Maps to canonical PCMN codes that may differ from
# bas:mXX codes for the same economic concept (e.g., bas:m110 EBIT = MAR 9901,
# but be-gaap-ci:OperatingResult = MAR 649 historically).
#
# Sparse mapping — populated as we encounter be-gaap-ci-flavored filings.
# Parser currently only handles bas: and pfs: namespaces; add be-gaap-ci handling
# when needed (W8-worker FU item).

BE_GAAP_CI_MAP = {
    # element name (without prefix) → (pcmn_code, dutch_label, section)
    "CashEquivalents":      ("54/58", "Liquide middelen",            "BS_A"),
    "OperatingProfitLoss":  ("649",   "Bedrijfswinst (verlies) (EBIT)", "IS"),
    "IncomeTaxExpense":     ("9134",  "Belastingen op het resultaat",   "IS"),
    # TODO: extend with more elements as we encounter them
}
