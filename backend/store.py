"""Single-process task history and web evaluation labels backed by SQLite."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import Lock
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.collection_sync_lock = Lock()
        self.reader_ask_lock = Lock()
        self.ranking_lock = Lock()
        self.ranking_model = None

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def init(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, nonce TEXT NOT NULL UNIQUE, mode TEXT NOT NULL,
                    focus TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'recommend',
                    research_scope TEXT NOT NULL DEFAULT 'original', library_item_id TEXT,
                    result TEXT, error TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_task ON tasks((1))
                    WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY, task_id TEXT NOT NULL, stage TEXT NOT NULL,
                    detail TEXT NOT NULL, at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_for_task ON events(task_id, seq);
                CREATE TABLE IF NOT EXISTS annotations (
                    case_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reading_library (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, author TEXT NOT NULL, source TEXT NOT NULL,
                    url TEXT, body TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discovery_ratings (
                    candidate_id TEXT PRIMARY KEY, title TEXT NOT NULL, grade INTEGER NOT NULL CHECK(grade BETWEEN 0 AND 2)
                );
                CREATE TABLE IF NOT EXISTS discovery_zhihu_collections (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discovery_hidden (
                    candidate_id TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS discovery_batches (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS discovery_batches_by_time ON discovery_batches(created_at);
                CREATE TABLE IF NOT EXISTS discovery_training_batches (
                    id TEXT PRIMARY KEY, shown_at TEXT NOT NULL, goal_key TEXT NOT NULL, cohort_key TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discovery_training_labels (
                    batch_id TEXT NOT NULL, candidate_id TEXT NOT NULL, title TEXT NOT NULL,
                    grade INTEGER NOT NULL CHECK(grade BETWEEN 0 AND 2),
                    original_rank INTEGER NOT NULL, band INTEGER NOT NULL, features TEXT NOT NULL, rated_at TEXT NOT NULL,
                    shown_rank INTEGER,
                    PRIMARY KEY(batch_id,candidate_id)
                );
                CREATE INDEX IF NOT EXISTS discovery_training_labels_by_item
                    ON discovery_training_labels(candidate_id,rated_at);
                CREATE TABLE IF NOT EXISTS discovery_evaluations (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, grades TEXT NOT NULL DEFAULT '{}'
                );
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(tasks)')}
            if 'kind' not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'recommend'")
            if 'research_scope' not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN research_scope TEXT NOT NULL DEFAULT 'original'")
            if 'library_item_id' not in columns:
                db.execute('ALTER TABLE tasks ADD COLUMN library_item_id TEXT')
            training_columns = {row['name'] for row in db.execute('PRAGMA table_info(discovery_training_labels)')}
            if 'shown_rank' not in training_columns:
                db.execute('ALTER TABLE discovery_training_labels ADD COLUMN shown_rank INTEGER')
            interrupted = db.execute(
                "UPDATE tasks SET status='interrupted', error=? WHERE status IN ('queued','running') RETURNING id",
                ('服务重启中断了任务，请手动发起新一轮。',),
            ).fetchall()
            for task in interrupted:
                self._event(db, task['id'], 'interrupted', '服务重启，任务停止。')

    def create(self, nonce, mode, focus, kind='recommend', research_scope='original', library_item_id=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id,mode,focus,kind,research_scope,library_item_id FROM tasks WHERE nonce=?', (nonce,)).fetchone()
            if existing:
                if (mode, focus, kind, research_scope, library_item_id) != tuple(existing)[1:]:
                    raise ValueError('请求编号已用于另一项任务。')
                return existing['id'], False
            task_id = uuid4().hex
            try:
                db.execute('INSERT INTO tasks(id,nonce,mode,focus,status,created_at,kind,research_scope,library_item_id) '
                           'VALUES (?,?,?,?,?,?,?,?,?)',
                           (task_id, nonce, mode, focus, 'queued', now(), kind, research_scope, library_item_id))
            except sqlite3.IntegrityError:
                raise ValueError('已有任务执行中，请等待完成。') from None
            self._event(db, task_id, 'queued', '任务已创建')
            return task_id, True

    @staticmethod
    def _event(db, task_id, stage, detail):
        db.execute('INSERT INTO events(task_id,stage,detail,at) VALUES (?,?,?,?)', (task_id, stage, detail, now()))

    def event(self, task_id, stage, detail):
        with self.connect() as db:
            self._event(db, task_id, stage, detail)

    def start(self, task_id):
        with self.connect() as db:
            return db.execute("UPDATE tasks SET status='running' WHERE id=? AND status='queued'", (task_id,)).rowcount == 1

    def finish(self, task_id, result=None, error=None):
        with self.connect() as db:
            status = 'failed' if error is not None else 'completed'
            changed = db.execute("UPDATE tasks SET status=?, result=?, error=? WHERE id=? AND status IN ('queued','running')",
                                 (status, json.dumps(result, ensure_ascii=False) if result is not None else None, error, task_id))
            if changed.rowcount:
                self._event(db, task_id, status, error or '报告已保存')

    def task(self, task_id):
        with self.connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT id,mode,focus,status,created_at,result,error,kind,research_scope,library_item_id '
                             'FROM tasks WHERE id=?', (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            events = db.execute('SELECT seq,stage,detail,at FROM events WHERE task_id=? ORDER BY seq', (task_id,))
            return {**dict(row), 'result': json.loads(row['result']) if row['result'] else None,
                    'events': [dict(event) for event in events]}

    def recent(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,mode,focus,status,created_at,kind FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT 30')]

    def annotations(self):
        with self.connect() as db:
            return {row['case_id']: json.loads(row['payload']) for row in db.execute('SELECT case_id,payload FROM annotations')}

    def save_annotation(self, annotation):
        with self.connect() as db:
            db.execute('''INSERT INTO annotations VALUES (?,?,?)
                ON CONFLICT(case_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at''',
                (annotation['case_id'], json.dumps(annotation, ensure_ascii=False), now()))

    def discovery_ratings(self):
        with self.connect() as db:
            rows = db.execute('SELECT candidate_id,title,grade FROM discovery_ratings')
            ratings = {row['candidate_id']: (row['grade'], row['title']) for row in rows}
            latest = db.execute('''SELECT candidate_id,title,grade FROM (
                SELECT candidate_id,title,grade, ROW_NUMBER() OVER (
                    PARTITION BY candidate_id ORDER BY rated_at DESC,rowid DESC
                ) AS priority FROM discovery_training_labels) WHERE priority=1''')
            ratings.update({row['candidate_id']: (row['grade'], row['title']) for row in latest})
            return ratings

    def discovery_saved_items(self):
        with self.connect() as db:
            rows = db.execute("""SELECT id,title,source,
                CASE WHEN source='note' THEN substr(body,1,1800) ELSE '' END AS excerpt
                FROM reading_library WHERE source != 'demo'
                ORDER BY updated_at DESC,id LIMIT 60""")
            return {row['id'].replace('-', ':', 1): dict(row) for row in rows}

    def hidden_discovery(self):
        with self.connect() as db:
            return {row['candidate_id'] for row in db.execute('SELECT candidate_id FROM discovery_hidden')}

    def set_discovery_hidden(self, candidate_id, hidden):
        with self.connect() as db:
            if hidden:
                db.execute('INSERT OR IGNORE INTO discovery_hidden VALUES (?)', (candidate_id,))
            else:
                db.execute('DELETE FROM discovery_hidden WHERE candidate_id=?', (candidate_id,))

    def zhihu_collections(self):
        with self.connect() as db:
            row = db.execute('SELECT payload,updated_at FROM discovery_zhihu_collections WHERE singleton = 1').fetchone()
        return {**json.loads(row['payload']), 'updated_at': row['updated_at']} if row else None

    def set_zhihu_collections(self, payload):
        updated_at = now()
        with self.connect() as db:
            db.execute('''INSERT INTO discovery_zhihu_collections VALUES (1,?,?)
                ON CONFLICT(singleton) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at''',
                (json.dumps(payload, ensure_ascii=False), updated_at))
        return {**payload, 'updated_at': updated_at}

    def save_discovery_batch(self, payload):
        batch_id = uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO discovery_batches VALUES (?,?,?)', (batch_id, json.dumps(payload, ensure_ascii=False), now()))
            db.execute("DELETE FROM discovery_batches WHERE created_at < strftime('%Y-%m-%dT%H:%M:%f+00:00','now','-24 hours')")
            db.execute('''DELETE FROM discovery_batches WHERE id IN (
                SELECT id FROM discovery_batches ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET 50
            )''')
        return batch_id

    def discovery_batch(self, batch_id):
        with self.connect() as db:
            row = db.execute("""SELECT payload FROM discovery_batches WHERE id=?
                AND created_at >= strftime('%Y-%m-%dT%H:%M:%f+00:00','now','-24 hours')""", (batch_id,)).fetchone()
        return json.loads(row['payload']) if row else None

    def discovery_evaluation(self, batch_id):
        with self.connect() as db:
            row = db.execute('SELECT payload,grades FROM discovery_evaluations WHERE id=?', (batch_id,)).fetchone()
        return (json.loads(row['payload']), json.loads(row['grades'])) if row else None

    def save_discovery_evaluation(self, batch_id, payload):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO discovery_evaluations(id,payload) VALUES (?,?)',
                       (batch_id, json.dumps(payload, ensure_ascii=False)))
        return self.discovery_evaluation(batch_id)

    def grade_discovery_evaluation(self, batch_id, candidate_id, grade):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload,grades FROM discovery_evaluations WHERE id=?', (batch_id,)).fetchone()
            if not row:
                return None
            payload, grades = json.loads(row['payload']), json.loads(row['grades'])
            if candidate_id not in {item['candidate_id'] for item in payload['items']}:
                return None
            if grade is None:
                grades.pop(candidate_id, None)
            else:
                grades[candidate_id] = grade
            db.execute('UPDATE discovery_evaluations SET grades=? WHERE id=?', (json.dumps(grades), batch_id))
        return payload, grades

    def train_discovery(self):
        from .pairwise_ranker import train

        with self.ranking_lock:
            if self.ranking_model is None:
                with self.connect() as db:
                    rows = db.execute("""SELECT batch_id,candidate_id,grade,original_rank,band,features,rated_at,
                            shown_at,goal_key,cohort_key FROM discovery_training_labels l
                            JOIN discovery_training_batches b ON b.id=l.batch_id
                            WHERE b.id IN (SELECT id FROM discovery_training_batches
                                           ORDER BY shown_at DESC, rowid DESC LIMIT 120)
                            ORDER BY shown_at,b.rowid,candidate_id""").fetchall()
                records = [dict(row, features=json.loads(row['features'])) for row in rows]
                self.ranking_model = train(records)
            return self.ranking_model

    def rate_discovery_batch(self, batch_id, candidate_id, grade):
        with self.ranking_lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            record = db.execute("""SELECT payload,created_at FROM discovery_batches WHERE id=?
                AND created_at >= strftime('%Y-%m-%dT%H:%M:%f+00:00','now','-24 hours')""", (batch_id,)).fetchone()
            if not record:
                return False
            batch = json.loads(record['payload'])
            shown = batch.get('shown_features', {})
            item = shown.get(candidate_id)
            if not item:
                return False
            if grade is None:
                deleted = db.execute('DELETE FROM discovery_training_labels WHERE batch_id=? AND candidate_id=?',
                                     (batch_id, candidate_id))
                if not deleted.rowcount:
                    return True
                db.execute('''DELETE FROM discovery_training_batches WHERE id=? AND NOT EXISTS (
                    SELECT 1 FROM discovery_training_labels WHERE batch_id=?)''', (batch_id, batch_id))
            else:
                cohort = sha256(','.join(sorted(shown)).encode()).hexdigest()
                goal = batch['goal'] if batch['goal_origin'] == 'manual' else batch['query']
                db.execute('INSERT OR IGNORE INTO discovery_training_batches VALUES (?,?,?,?)',
                           (batch_id, record['created_at'], goal.casefold(), cohort))
                db.execute('''INSERT INTO discovery_training_labels
                    (batch_id,candidate_id,title,grade,original_rank,band,features,rated_at,shown_rank)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(batch_id,candidate_id) DO UPDATE SET grade=excluded.grade, rated_at=excluded.rated_at
                    WHERE grade != excluded.grade''',
                    (batch_id, candidate_id, item['title'], grade, item['original_rank'], item['band'],
                     json.dumps(item['values']), now(), item.get('shown_rank', batch['initial_ranks'].get(candidate_id))))
            self.ranking_model = None
            return True

    def rate_discovery(self, candidate_id, title, grade):
        with self.connect() as db:
            self._set_rating(db, candidate_id, title, grade)

    @staticmethod
    def _set_rating(db, candidate_id, title, grade):
        if grade is None:
            db.execute('DELETE FROM discovery_ratings WHERE candidate_id=?', (candidate_id,))
        else:
            db.execute('INSERT INTO discovery_ratings VALUES (?,?,?) '
                       'ON CONFLICT(candidate_id) DO UPDATE SET title=excluded.title, grade=excluded.grade',
                       (candidate_id, title, grade))
