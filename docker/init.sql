-- PostgreSQL initialization script

-- Create user if not exists
DO
$$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rag_flow') THEN
        CREATE USER rag_flow WITH PASSWORD 'infini_rag_flow';
    END IF;
END
$$;

-- Create database if not exists
DO
$$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'rag_flow') THEN
        CREATE DATABASE rag_flow OWNER rag_flow;
    END IF;
END
$$;

-- Connect to the database
\c rag_flow;

-- Install vector extension
CREATE EXTENSION IF NOT EXISTS vector;