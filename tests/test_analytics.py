from datetime import date
from pathlib import Path
import tempfile
import unittest
from app.database import Database
from app.services.analytics import period_bounds, load_report


class AnalyticsTest(unittest.TestCase):
    def test_calendar_boundaries(self):
        self.assertEqual(period_bounds(date(2024,2,29),'month'),(date(2024,2,1),date(2024,3,1)))
        self.assertEqual(period_bounds(date(2026,9,16),'week'),(date(2026,9,14),date(2026,9,21)))

    def test_global_history_date_filter_base_links_and_keywords(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Database(Path(folder)/'test.db')
            with db.connect() as con:
                for i,(category,day) in enumerate((('ALERTA','2026-09-16'),('NORMAL','2026-09-15')),1):
                    con.execute("INSERT INTO calls(id,filename,file_path,status,category,processed_at,reviewed) VALUES (?,?,?,'COMPLETADO',?,datetime(?,'utc'),1)",
                                (i,f'call{i}.wav',f'call{i}.wav',category,f'{day} 12:00:00'))
                con.execute("INSERT INTO keyword_hits(call_id,keyword,timestamp_seconds,is_risk_validated) VALUES (1,'queja',10,1),(1,'queja',20,1)")
            db.record_analysis_base(1,'base.xlsx')
            db.record_analysis_base(1,'base.xlsx')
            with db.connect() as con:
                con.execute("UPDATE call_bases SET analyzed_at=datetime('2026-09-16 12:00:00','utc')")
            r=load_report(db,date(2026,9,16),date(2026,9,17))
            self.assertEqual(r['total'],1)
            self.assertEqual(r['incidents'],1)
            self.assertEqual(r['verified'],1)
            self.assertEqual(r['keywords'],[('queja',2,1)])
            self.assertEqual(r['bases'][0]['calls'],1)
            self.assertEqual(r['calls'][0]['keywords'],'queja')
            self.assertEqual(load_report(db,date(2026,9,14),date(2026,9,21))['total'],2)
            self.assertEqual(load_report(db,date(2025,9,14),date(2025,9,21))['total'],0)
