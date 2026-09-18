from __future__ import annotations

import sqlite3

DB = r"C:\Users\Administrator\Documents\remotion\xuetu-lite-rebuild\data\processed\xuetu_lite.db"


def main() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    print("== videos by major/platform ==")
    for row in con.execute(
        """
        SELECT COALESCE(major, 'NULL') AS m, platform AS p, COUNT(*) AS c
        FROM video GROUP BY m, p ORDER BY m, p
        """
    ):
        print(dict(row))
    print("== learning records by demo account ==")
    for row in con.execute(
        """
        SELECT student_id, COUNT(*) AS total,
               SUM(progress >= 0.25) AS positive_count,
               SUM(status = 'completed') AS completed_count
        FROM learning_record GROUP BY student_id
        """
    ):
        print(dict(row))
    print("== recommendable remaining candidates when a major's 5 learned videos are excluded ==")
    for row in con.execute(
        """
        SELECT v.major, COUNT(*) AS total, COUNT(v2.video_id) AS learned
        FROM video v
        LEFT JOIN (
          SELECT DISTINCT l.video_id
          FROM learning_record l JOIN video vv ON vv.video_id = l.video_id
          WHERE l.student_id IN ('20240101','20240102','20240103','20240104','20240105')
        ) v2 ON v2.video_id = v.video_id
        WHERE v.major IS NOT NULL AND v.major <> ''
        GROUP BY v.major
        """
    ):
        print(dict(row))
    con.close()


if __name__ == "__main__":
    main()
