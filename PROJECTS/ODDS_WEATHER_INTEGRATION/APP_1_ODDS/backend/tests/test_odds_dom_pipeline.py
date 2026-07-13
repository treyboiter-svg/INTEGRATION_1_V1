import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.fetcher import _parse_market_cell, _extract_american

def cell(raw, nodes=None, attrs=None): return {"raw":raw,"nodes":nodes or [],"attrs":attrs or {}}
def node(text, cls="", attrs=None): return {"text":text,"class":cls,"attrs":attrs or {}}

def test_spread_recovers_price_from_structured_dom():
    c=cell("+1.5 -110", [node("+1.5", "data-value"),node("-110", "data-moneyline")])
    x=_parse_market_cell(c,"spread"); assert x["point"]==1.5 and x["american"]==-110 and x["parse_status"]=="accepted"
def test_total_recovers_price_and_total_from_context():
    c=cell("O 8.5 -105", [node("O 8.5", "data-value"),node("-105", "data-odds")])
    x=_parse_market_cell(c,"total"); assert x["point"]==8.5 and x["american"]==-105
def test_moneyline_uses_explicit_price_attribute():
    c=cell("110", [node("110", "", {"data-moneyline":"110"})])
    x=_parse_market_cell(c,"moneyline"); assert x["american"]==110

def test_line_is_never_promoted_to_price():
    x=_parse_market_cell(cell("O 181.5",[node("O 181.5","data-value")]),"total")
    assert x["point"]==181.5 and x["american"] is None and x["parse_status"]=="rejected_missing_explicit_price"
def test_conflicting_price_evidence_is_disqualified():
    c=cell("-110 -115",[node("-110","data-moneyline"),node("-115","data-moneyline")])
    assert _extract_american(c)==(None,"ambiguous_price_candidates")
def test_raw_signed_price_is_valid_fallback():
    x=_parse_market_cell(cell("-4.5 +102"),"spread"); assert x["point"]==-4.5 and x["american"]==102
