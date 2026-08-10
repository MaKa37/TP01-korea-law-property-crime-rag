"""PostgreSQL 커넥션 풀 관리."""
import logging

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

from core.config import RAGConfig


def create_db_pool(config: RAGConfig, logger: logging.Logger) -> ThreadedConnectionPool:
    try:
        pool = ThreadedConnectionPool(
            minconn=config.db_pool_min,
            maxconn=config.db_pool_max,
            host=config.db_host,
            port=config.db_port,
            dbname=config.db_name,
            user=config.db_user,
            password=config.db_pass
        )
        logger.info("✅ PostgreSQL 커넥션 풀 초기화 완료")
        return pool
    except psycopg2.OperationalError as e:
        logger.error(f"🚨 데이터베이스 연결 실패: {e}")
        raise
