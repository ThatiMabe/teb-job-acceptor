"""Rehearses what the agent sends for accepted / skipped / needs-you jobs, with Telegram, Notion and the
job log stubbed out: nothing is sent or written.   .venv\\Scripts\\python.exe tests\\rehearse_alerts.py"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import agent_server as a  # noqa: E402

a.CAPTURES = pathlib.Path(tempfile.mkdtemp())
sent, notion = [], []
a.telegram = lambda msg, photo=None: sent.append(("[photo] " if photo else "") + msg)
a.notion_async = lambda *x, **k: notion.append((x[0], x[1], k.get("status")))
a.record_jobs = lambda *x, **k: None
shot = a.CAPTURES / "after.jpg"
shot.write_bytes(b"x")
a.save_screenshot = lambda sid, data: shot

kandua = {"id": "kandua", "name": "Kandua Pro", "jobs_url": "https://kandua-pro.web.app/new-jobs", "accept_kind": "kandua"}
f = {"title": "Electrical fixtures repair", "date_time": "06 November 2026", "location": "Testville",
     "description": "Electrical fixtures repair Need a light fitting replaced in the lounge. ACCEPT"}
acc = {
    "attempted": ["k-1", "k-2", "k-3"], "after_text": "",
    "accepted": [{"job_id": "k-1", "fields": f, "verify_text": "Job accepted You have accepted this job."}],
    "skipped_jobs": [{"job_id": "k-2", "fields": {**f, "title": "Microwave repair"}, "reason": 'mentions "microwave"'}],
    "needs_user": {"job_id": "k-3", "fields": f, "reason": "No Accept button found on the job page.",
                   "buttons": ["SEND QUOTE", "DECLINE"], "url": "https://kandua-pro.web.app/marketplace-jobs/abc"},
}
d = {"jobs": 0}
a.handle_accept(kandua, acc, {"html": "<html/>", "screenshot_before": "x"}, "https://x", "page", shot, d)
for m in sent:
    print(m, "\n")
print("notion rows:", notion)
print("counters:", d)
