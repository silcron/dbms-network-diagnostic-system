"""Create the course demo schema and a least-privilege local monitor account."""

from __future__ import annotations

import os

import pymysql


def main() -> None:
    root_password = os.environ["COURSE_ROOT_PASSWORD"]
    monitor_password = os.environ["COURSE_MONITOR_PASSWORD"]
    connection = pymysql.connect(
        host="127.0.0.1",
        port=int(os.environ.get("DB_PORT", "3306")),
        user="root",
        password=root_password,
        autocommit=True,
        connect_timeout=3,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("CREATE DATABASE IF NOT EXISTS `course_network_demo` CHARACTER SET utf8mb4")
            cursor.execute("CREATE USER IF NOT EXISTS 'course_monitor'@'localhost' IDENTIFIED BY %s", (monitor_password,))
            cursor.execute("ALTER USER 'course_monitor'@'localhost' IDENTIFIED BY %s", (monitor_password,))
            cursor.execute("GRANT SELECT ON `course_network_demo`.* TO 'course_monitor'@'localhost'")
    finally:
        connection.close()
    print("Created course_network_demo and local read-only course_monitor account.")


if __name__ == "__main__":
    main()
