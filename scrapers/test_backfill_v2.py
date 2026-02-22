from backfill_calendar import CalendarBackfill
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)

class TestBackfill(CalendarBackfill):
    def run(self):
        # Only run History2.html
        fpath = Path("c:/MacroLens/backend/scrapers/History2.html")
        if fpath.exists():
            events = self.parse_html_file(fpath)
            logging.info(f"Found {len(events)} events in History2.html")
            # print first 5 to verify
            for e in events[:5]:
                print(e)
            self.save_to_db(events)

if __name__ == "__main__":
    tb = TestBackfill()
    tb.run()
