#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
import logging
import time

from common import settings
from common.decorator import singleton


@singleton
class PGConnectionPool:

    def __init__(self):
        if hasattr(settings, "POSTGRESQL") and settings.POSTGRESQL:
            self.DATABASE_CONFIG = settings.POSTGRESQL
        else:
            self.DATABASE_CONFIG = settings.get_base_config("postgresql", {
                "host": "localhost",
                "port": 5432,
                "name": "postgres",
                "user": "postgres",
                "password": ""
            })

        self.conn_pool = self._create_connection_pool()
        self._init_pool()

    def _create_connection_pool(self):
        """
        Create connection pool
        """
        try:
            return ThreadedConnectionPool(
                minconn=1,
                maxconn=10,
                host=self.DATABASE_CONFIG.get("host", "localhost"),
                port=self.DATABASE_CONFIG.get("port", 5432),
                database=self.DATABASE_CONFIG.get("name", "postgres"),
                user=self.DATABASE_CONFIG.get("user", "postgres"),
                password=self.DATABASE_CONFIG.get("password", "")
            )
        except Exception as e:
            logging.error(f"Failed to create connection pool: {e}")
            raise

    def _init_pool(self):
        """
        Initialize connection pool
        """
        conn = None
        try:
            conn = self.conn_pool.getconn()
            cursor = conn.cursor()
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.commit()
            logging.info("Connection pool initialized successfully")
        except Exception as e:
            logging.error(f"Failed to initialize connection pool: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                self.conn_pool.putconn(conn)

    def get_conn_pool(self):
        return self.conn_pool

    def refresh_conn_pool(self):
        try:
            conn = self.conn_pool.getconn()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            return self.conn_pool
        except Exception as e:
            logging.error(str(e))
            if hasattr(self, "conn_pool") and self.conn_pool:
                self.conn_pool = self._create_connection_pool()
                self._init_pool()
                return self.conn_pool

    def __del__(self):
        if hasattr(self, "conn_pool") and self.conn_pool:
            self.conn_pool.closeall()


PG_CONN = PGConnectionPool()