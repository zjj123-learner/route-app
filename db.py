# -*- coding: utf-8 -*-
import os
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "route.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """建表: places 记住去过的地点, plans 存历史计划, prefs 存休息偏好"""
    conn = _conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS places (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                name TEXT NOT NULL,
                address TEXT DEFAULT '',
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                source TEXT DEFAULT 'poi',
                last_used INTEGER DEFAULT 0
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_places_kw ON places(keyword)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER DEFAULT 0,
                mode TEXT DEFAULT 'walk',
                start_name TEXT DEFAULT '家',
                start_lat REAL,
                start_lng REAL,
                summary TEXT,
                stops_json TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period TEXT UNIQUE,
                value TEXT DEFAULT 'home'
            )""")
        conn.commit()
    finally:
        conn.close()


def remember_place(keyword, name, lat, lng, address="", source="poi"):
    """把用户实际用到的地点记下来, 同关键词同地点只更新使用时间"""
    conn = _conn()
    try:
        conn.execute("DELETE FROM places WHERE keyword=? AND name=?", (keyword, name))
        conn.execute(
            "INSERT INTO places(keyword, name, address, lat, lng, source, last_used) VALUES(?,?,?,?,?,?,?)",
            (keyword, name, address, lat, lng, source, int(time.time() * 1000)))
        conn.commit()
    finally:
        conn.close()


def search_places(keyword, limit=5):
    """查'去过的地点', 按最近使用排序, 返回 [{name, address, lat, lng, last_used}]"""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT name, address, lat, lng, last_used FROM places "
            "WHERE keyword=? ORDER BY last_used DESC, id DESC LIMIT ?",
            (keyword, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_plan(mode, start_name, start_lat, start_lng, summary, stops_json):
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO plans(created_at, mode, start_name, start_lat, start_lng, summary, stops_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (int(time.time()), mode, start_name, start_lat, start_lng, summary, stops_json))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_plans(limit=10):
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, created_at, mode, start_name, summary FROM plans "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_plan(plan_id):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def clear_plans():
    """清空全部历史计划"""
    conn = _conn()
    try:
        conn.execute("DELETE FROM plans")
        conn.commit()
    finally:
        conn.close()

def save_prefs(prefs):
    conn = _conn()
    try:
        for period, value in prefs.items():
            conn.execute(
                "INSERT INTO prefs(period, value) VALUES(?,?) "
                "ON CONFLICT(period) DO UPDATE SET value=excluded.value",
                (period, value))
        conn.commit()
    finally:
        conn.close()


def load_prefs():
    conn = _conn()
    try:
        rows = conn.execute("SELECT period, value FROM prefs").fetchall()
        return {r["period"]: r["value"] for r in rows}
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    remember_place("银行", "招商银行", 31.23, 121.47, "上海南京东路")
    print(search_places("银行"))