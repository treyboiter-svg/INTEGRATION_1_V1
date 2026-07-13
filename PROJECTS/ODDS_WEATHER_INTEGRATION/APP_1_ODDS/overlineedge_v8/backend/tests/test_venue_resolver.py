import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.venue_resolver import VenueResolver

ROOT=Path(__file__).resolve().parents[2]
r=VenueResolver(ROOT / "US_SPORTS_VENUES_MASTER_CORRECTED_V2.xlsx")

def test_mlb_pitcher_noise_and_compound_mascots():
    g=r.validate_game("mlb", "RedSoxSandoval(L)", "WhiteSoxKay(L)")
    assert g["accepted"]
    assert g["away"].team == "Boston Red Sox"
    assert g["home"].team == "Chicago White Sox"

def test_market_requires_both_teams():
    g=r.validate_game("mlb", "AthleticsSuarez(L)", "TigersValdez(L)")
    assert r.match_market(g, ["Will the Oakland Athletics beat the Detroit Tigers?"])[0]
    assert r.match_market(g, ["Will the Athletics win today?"])[0] is None
