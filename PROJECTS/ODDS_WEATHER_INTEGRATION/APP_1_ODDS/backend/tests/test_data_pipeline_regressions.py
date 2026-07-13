import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.fetcher import _parse_market_cell, _parse_kalshi, _parse_poly
from app.services.venue_resolver import VenueResolver
ROOT=Path(__file__).resolve().parents[2]; R=VenueResolver(ROOT/"US_SPORTS_VENUES_MASTER_CORRECTED_V2.xlsx")
def test_logged_mlb_pitcher_labels_resolve():
 for a,h,ea,eh in [("Brewers Sproat (R)","Pirates Ashcraft (R)","Milwaukee Brewers","Pittsburgh Pirates"),("Red Sox Gray (R)","Mets Mc Lean (R)","Boston Red Sox","New York Mets"),("White Sox Burke (R)","Athletics Lopez (L)","Chicago White Sox","Oakland Athletics")]:
  x=R.validate_game("mlb",a,h);assert x["accepted"] and x["away"].team==ea and x["home"].team==eh
def test_line_and_price_require_correct_context():
 assert _parse_market_cell({"raw":"+3.5 -110"},"spread")["american"]==-110
 assert _parse_market_cell({"raw":"o181.5 -105"},"total")["american"]==-105
 assert _parse_market_cell({"raw":"o181.5"},"total")["american"] is None
def test_prediction_parsers_keep_context():
 assert _parse_kalshi([{"title":"Will Milwaukee Brewers win?","last_price":57}])
 assert _parse_poly([{"_event_context":"Brewers vs Pirates","question":"Brewers win?","outcomes":["Yes","No"],"outcomePrices":[".55",".45"]}])
