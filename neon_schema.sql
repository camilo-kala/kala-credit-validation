-- KALA Credit Validation Audit Schema for Neon PostgreSQL
-- Run this in Neon SQL Editor before deploying

CREATE TABLE IF NOT EXISTS credit_validation_audit (
    id SERIAL PRIMARY KEY,
    
    -- Identifiers
    transaction_id VARCHAR(36) NOT NULL,
    person_id VARCHAR(36),
    
    -- Input snapshots (JSONB for better querying in Neon)
    input_ocr JSONB,
    input_buro JSONB,
    input_truora JSONB,
    input_tasks JSONB,
    consolidated_prompt TEXT,
    
    -- Claude response
    claude_response_raw TEXT,
    claude_response_parsed JSONB,
    
    -- Extracted dictamen fields
    decision VARCHAR(20),
    producto VARCHAR(30),
    monto_maximo NUMERIC(15, 2),
    plazo_maximo INTEGER,
    capacidad_disponible NUMERIC(15, 2),
    tiene_inaceptables BOOLEAN,
    cantidad_embargos INTEGER,
    procesos_demandado_60m INTEGER,
    resumen VARCHAR(300),
    
    -- Execution metrics
    tokens_input INTEGER,
    tokens_output INTEGER,
    latency_kala_api_ms INTEGER,
    latency_claude_ms INTEGER,
    latency_total_ms INTEGER,
    claude_retries INTEGER DEFAULT 0,
    
    -- Metadata
    model_version VARCHAR(50) NOT NULL,
    prompt_version VARCHAR(20) DEFAULT '1.0.0',
    status VARCHAR(20) DEFAULT 'SUCCESS',
    error_message TEXT,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by VARCHAR(100)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_cva_transaction_id ON credit_validation_audit(transaction_id);
CREATE INDEX IF NOT EXISTS idx_cva_decision ON credit_validation_audit(decision);
CREATE INDEX IF NOT EXISTS idx_cva_status ON credit_validation_audit(status);
CREATE INDEX IF NOT EXISTS idx_cva_created_at ON credit_validation_audit(created_at DESC);

-- Composite index for transaction history
CREATE INDEX IF NOT EXISTS idx_cva_txn_created ON credit_validation_audit(transaction_id, created_at DESC);
