"""
KALA Credit Validation AI Agent - Vercel Serverless
"""

import os
import json
import hashlib
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import anthropic
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

# =============================================================================
# CONFIGURATION
# =============================================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KALA_API_BASE = os.getenv("KALA_API_BASE", "https://api.kalaplatform.tech")
KALA_AUTH_EMAIL = os.getenv("KALA_AUTH_EMAIL")
KALA_AUTH_PASSWORD = os.getenv("KALA_AUTH_PASSWORD")
DATABASE_URL = os.getenv("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
API_KEY_SECRET = os.getenv("API_KEY_SECRET", "kala-credit-validation-api-key-2024")

MAX_CLAUDE_RETRIES = 2
PROMPT_VERSION = "1.0.0"

# =============================================================================
# DATABASE SETUP
# =============================================================================

engine = None
SessionLocal = None
CreditValidationAudit = None
Base = None

if DATABASE_URL and DATABASE_URL.startswith("postgresql"):
    try:
        from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, Boolean, JSON
        from sqlalchemy.orm import declarative_base, sessionmaker
        
        Base = declarative_base()
        
        class CreditValidationAudit(Base):
            __tablename__ = "credit_validation_audit"
            id = Column(Integer, primary_key=True, autoincrement=True)
            transaction_id = Column(String(36), index=True, nullable=False)
            person_id = Column(String(36), nullable=True)
            input_ocr = Column(JSON, nullable=True)
            input_buro = Column(JSON, nullable=True)
            input_truora = Column(JSON, nullable=True)
            input_tasks = Column(JSON, nullable=True)
            consolidated_prompt = Column(Text, nullable=True)
            claude_response_raw = Column(Text, nullable=True)
            claude_response_parsed = Column(JSON, nullable=True)
            decision = Column(String(20), index=True, nullable=True)
            producto = Column(String(30), nullable=True)
            monto_maximo = Column(Float, nullable=True)
            plazo_maximo = Column(Integer, nullable=True)
            capacidad_disponible = Column(Float, nullable=True)
            tiene_inaceptables = Column(Boolean, nullable=True)
            cantidad_embargos = Column(Integer, nullable=True)
            procesos_demandado_60m = Column(Integer, nullable=True)
            resumen = Column(String(300), nullable=True)
            tokens_input = Column(Integer, nullable=True)
            tokens_output = Column(Integer, nullable=True)
            latency_kala_api_ms = Column(Integer, nullable=True)
            latency_claude_ms = Column(Integer, nullable=True)
            latency_total_ms = Column(Integer, nullable=True)
            claude_retries = Column(Integer, default=0)
            model_version = Column(String(50), nullable=False)
            prompt_version = Column(String(20), default="1.0.0")
            status = Column(String(20), index=True, default="SUCCESS")
            error_message = Column(Text, nullable=True)
            created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
            created_by = Column(String(100), nullable=True)
        
        db_url = DATABASE_URL
        if "sslmode" not in db_url:
            db_url = f"{db_url}?sslmode=require"
        
        engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=300)
        SessionLocal = sessionmaker(bind=engine)
        Base.metadata.create_all(bind=engine)
        logger.info("Database configured successfully")
    except Exception as e:
        logger.error(f"Database configuration failed: {e}")


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class ValidationRequest(BaseModel):
    transaction_id: str = Field(..., description="UUID de la transacción")


class ValidationResponse(BaseModel):
    transaction_id: str
    status: str
    decision: Optional[str] = None
    producto: Optional[str] = None
    monto_maximo: Optional[float] = None
    plazo_maximo: Optional[int] = None
    capacidad_disponible: Optional[float] = None
    resumen: Optional[str] = None
    dictamen_completo: Optional[dict] = None
    error: Optional[str] = None
    latency_ms: Optional[int] = None
    audit_id: Optional[int] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    claude_api: str
    kala_api: str


# =============================================================================
# TOKEN CACHE
# =============================================================================

class TokenCache:
    _token: Optional[str] = None
    _expires_at: Optional[datetime] = None
    
    @classmethod
    def is_valid(cls) -> bool:
        if not cls._token or not cls._expires_at:
            return False
        return datetime.now(timezone.utc) < (cls._expires_at - timedelta(minutes=5))
    
    @classmethod
    def set_token(cls, token: str, expires_in: int = 3600):
        cls._token = token
        cls._expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    
    @classmethod
    def get_token(cls) -> Optional[str]:
        return cls._token if cls.is_valid() else None


# =============================================================================
# KALA API CLIENT
# =============================================================================

class KalaAPIClient:
    def __init__(self):
        self.base_url = KALA_API_BASE
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json"
        }
    
    def _ensure_token(self, client: httpx.Client) -> str:
        token = TokenCache.get_token()
        if token:
            return token
        
        response = client.post(
            f"{self.base_url}/v2/auth",
            json={"email": KALA_AUTH_EMAIL, "password": KALA_AUTH_PASSWORD},
            headers=self.headers
        )
        response.raise_for_status()
        data = response.json()
        
        token = data.get("token")
        if not token:
            raise HTTPException(status_code=500, detail="Failed to get Kala API token")
        
        TokenCache.set_token(token, data.get("expiresIn", 3600))
        return token
    
    def get_transaction_data(self, transaction_id: str) -> dict:
        start_time = datetime.now(timezone.utc)
        
        with httpx.Client(timeout=60.0) as client:
            token = self._ensure_token(client)
            auth_headers = {**self.headers, "Authorization": f"Bearer {token}"}
            
            tasks_resp = client.get(
                f"{self.base_url}/v2/task_inbox",
                params={"transactionId": transaction_id, "namesFrom": "TRUORA, BURO, GENERAL"},
                headers=auth_headers
            )
            tasks_resp.raise_for_status()
            tasks_data = tasks_resp.json()
            
            person_resp = client.get(
                f"{self.base_url}/v2/person/transaction/{transaction_id}/applicant",
                headers=auth_headers
            )
            person_resp.raise_for_status()
            person_id = person_resp.json().get("id")
            
            if not person_id:
                raise HTTPException(status_code=404, detail=f"Person not found for transaction {transaction_id}")
            
            extdata_resp = client.get(
                f"{self.base_url}/external_data/person/{person_id}",
                headers=auth_headers
            )
            extdata_resp.raise_for_status()
            extdata = extdata_resp.json()
        
        elapsed_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
        
        ocr = extdata.get("summaryTrebolOcr")
        buro = extdata.get("customSummaryBuro")
        truora = extdata.get("summaryTruoraBackgroundChecks")
        
        if not ocr:
            raise HTTPException(status_code=422, detail="OCR data not available")
        if not buro:
            raise HTTPException(status_code=422, detail="Buró data not available")
        if not truora:
            raise HTTPException(status_code=422, detail="Truora data not available")
        
        return {
            "person_id": person_id, "ocr": ocr, "buro": buro, "truora": truora,
            "tasks": tasks_data if isinstance(tasks_data, list) else [],
            "latency_ms": elapsed_ms
        }


# =============================================================================
# DATA CONSOLIDATION
# =============================================================================

def consolidate_data(txn_id: str, ocr: list, buro: dict, truora: dict, tasks: list) -> dict:
    ocr_doc = ocr[0] if ocr else {}
    std = ocr_doc.get("standardizedData", {})
    salary = std.get("salary_info", {})
    personal = std.get("personal_info", {})
    employment = std.get("employment_info", {})
    
    pagaduria = employment.get("company_name") or employment.get("employer_name") or "DESCONOCIDA"
    pag_upper = pagaduria.upper()
    pag_type = "OTRAS"
    for t in ["COLPENSIONES", "FOPEP", "FIDUPREVISORA", "CASUR", "CREMIL", "POSITIVA"]:
        if t in pag_upper:
            pag_type = t
            break
    
    deductions = salary.get("deduction_details", [])
    libranzas_ocr, embargos_ocr = [], []
    
    for d in deductions:
        desc = (d.get("description") or "").upper()
        if any(k in desc for k in ["LIBRANZA", "PRESTAMO", "CREDITO", "BCO", "BANCO"]):
            libranzas_ocr.append({"descripcion": d.get("description"), "monto": d.get("amount")})
        if "EMBARGO" in desc:
            embargos_ocr.append({"descripcion": d.get("description"), "monto": d.get("amount")})
    
    loans_buro = []
    for loan in buro.get("outstandingLoans", []):
        acc = loan.get("accounts", {})
        loans_buro.append({
            "entity": acc.get("lenderName"), "type": acc.get("accountType"),
            "debt": int(acc.get("totalDebt") or 0), "installment": int(acc.get("installments") or 0),
            "isLibranza": acc.get("typePayrollDeductionLoan"), "pastDueMax": acc.get("pastDueMax"),
            "paymentBehavior12m": (acc.get("paymentBehavior") or "")[:12], "sector": acc.get("sector")
        })
    
    enrichment = truora.get("enrichment", {})
    processes = [{"entity": p.get("entity"), "roleDefendant": p.get("roleDefendant"),
                  "processOpen": p.get("processOpen"), "processType": p.get("processType")}
                 for p in (enrichment.get("processes") or [])]
    
    return {
        "txn": txn_id,
        "ocr": {
            "personal": personal, "pagaduria": pagaduria, "pagaduriaType": pag_type,
            "salary": {"gross": salary.get("gross_salary"), "net": salary.get("net_salary")},
            "deductions": [{"description": d.get("description"), "amount": d.get("amount")} for d in deductions],
            "libranzasIdentificadas": libranzas_ocr, "embargos": embargos_ocr, "cantidadEmbargos": len(embargos_ocr)
        },
        "buro": {
            "score": buro.get("score", {}).get("scoring"),
            "name": buro.get("basicInformation", {}).get("fullName"),
            "cc": buro.get("basicInformation", {}).get("documentIdentificationNumber"),
            "alerts": buro.get("alert"), "loans": loans_buro
        },
        "truora": {
            "enrichment": {"sarlaftCompliance": enrichment.get("sarlaftCompliance"),
                          "numberOfProcesses": enrichment.get("numberOfProcesses")},
            "processes": processes
        },
        "tasks": [{"id": t.get("id"), "source": t.get("nameFrom"), "allValidated": t.get("allTaskValidated")} for t in tasks]
    }


# =============================================================================
# SYSTEM PROMPT
# =============================================================================

SYSTEM_PROMPT = """# ROL
Eres analista de crédito de KALA. Evalúas solicitudes de libranza para pensionados.

# REGLA FUNDAMENTAL
NO hagas inferencias sobre atributos NO regulados. Solo rechaza por criterios EXPLÍCITOS.

# CLIENTES INACEPTABLES
- Ingreso < 1 SMMLV (~$1,300,000)
- Listas restrictivas (SARLAFT false)
- Fallecidos en buró
- 5+ procesos ejecutivos como DEMANDADO (últimos 60 meses)
- >1 embargo en desprendible

# CAPACIDAD DE PAGO
Capacidad = (Pensión Bruta / 2) - Descuentos de ley - Descuentos libranza - Resguardo($2,500)

# FORMATO JSON
```json
{
  "txn": "string",
  "solicitante": {"nombre": "string", "cc": "string", "pagaduria": "string", "pagaduriaType": "string", "pensionBruta": 0, "pensionNeta": 0},
  "inaceptables": {"tiene": false, "criterios": []},
  "embargos": {"cantidadEnDesprendible": 0, "excedeLimite": false},
  "procesosJudiciales": {"totalComoDemandado60m": 0, "excedeLimite5": false},
  "capacidadPago": {"pensionBruta": 0, "base50pct": 0, "descuentosLey": 0, "descuentosLibranza": 0, "resguardo": 0, "capacidadDisponible": 0},
  "dictamen": {"decision": "APROBADO|CONDICIONADO|RECHAZADO", "producto": "LIBRE_INVERSION|COMPRA_CARTERA|AMBOS|NO_APLICA", "montoMaximo": 0, "plazoMaximo": 144, "condiciones": [], "motivosRechazo": []},
  "resumen": "string max 250 chars"
}
```
Responde ÚNICAMENTE JSON válido."""


# =============================================================================
# CLAUDE CLIENT
# =============================================================================

def call_claude(consolidated: dict) -> tuple[dict, dict]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    user_prompt = f"Analiza esta solicitud:\n{json.dumps(consolidated, indent=2, ensure_ascii=False)}"
    
    metrics = {"retries": 0, "tokens_input": 0, "tokens_output": 0, "latency_ms": 0, "raw_response": None}
    
    for attempt in range(MAX_CLAUDE_RETRIES + 1):
        try:
            start = datetime.now(timezone.utc)
            response = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=4096, temperature=0.1,
                system=SYSTEM_PROMPT, messages=[{"role": "user", "content": user_prompt}]
            )
            elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            
            raw = response.content[0].text if response.content else ""
            metrics.update({
                "raw_response": raw, "tokens_input": response.usage.input_tokens,
                "tokens_output": response.usage.output_tokens, "latency_ms": elapsed, "retries": attempt
            })
            
            match = re.search(r'\{[\s\S]*\}', raw)
            if match:
                return json.loads(match.group()), metrics
            raise ValueError("No JSON found")
        except Exception as e:
            if attempt == MAX_CLAUDE_RETRIES:
                raise


# =============================================================================
# API KEY AUTH
# =============================================================================

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Depends(api_key_header)) -> str:
    if not api_key:
        raise HTTPException(status_code=401, detail="API Key required")
    if hashlib.sha256(api_key.encode()).hexdigest() != hashlib.sha256(API_KEY_SECRET.encode()).hexdigest():
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return api_key


# =============================================================================
# FASTAPI APP
# =============================================================================

app = FastAPI(title="KALA Credit Validation", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

kala_client = KalaAPIClient()


@app.get("/")
def root():
    return {"message": "KALA Credit Validation API", "version": "1.0.0"}


@app.get("/health", response_model=HealthResponse)
def health_check():
    return HealthResponse(
        status="healthy", version="1.0.0",
        database="configured" if engine else "not_configured",
        claude_api="configured" if ANTHROPIC_API_KEY else "not_configured",
        kala_api="configured" if KALA_AUTH_EMAIL else "not_configured"
    )


@app.post("/api/v1/validate", response_model=ValidationResponse)
def validate_credit(request: ValidationRequest, api_key: str = Depends(verify_api_key)):
    start = datetime.now(timezone.utc)
    txn_id = request.transaction_id
    
    db, audit = None, None
    if SessionLocal and CreditValidationAudit:
        db = SessionLocal()
        audit = CreditValidationAudit(transaction_id=txn_id, model_version=CLAUDE_MODEL, prompt_version=PROMPT_VERSION, status="PROCESSING")
    
    try:
        data = kala_client.get_transaction_data(txn_id)
        
        if audit:
            audit.person_id = data["person_id"]
            audit.input_ocr = data["ocr"]
            audit.input_buro = data["buro"]
            audit.input_truora = data["truora"]
            audit.latency_kala_api_ms = data["latency_ms"]
        
        consolidated = consolidate_data(txn_id, data["ocr"], data["buro"], data["truora"], data["tasks"])
        
        if not ANTHROPIC_API_KEY:
            raise HTTPException(status_code=500, detail="Claude API not configured")
        
        parsed, metrics = call_claude(consolidated)
        
        if audit:
            audit.claude_response_parsed = parsed
            audit.tokens_input = metrics["tokens_input"]
            audit.tokens_output = metrics["tokens_output"]
            audit.latency_claude_ms = metrics["latency_ms"]
        
        dictamen = parsed.get("dictamen", {})
        capacidad = parsed.get("capacidadPago", {})
        
        if audit:
            audit.decision = dictamen.get("decision")
            audit.producto = dictamen.get("producto")
            audit.monto_maximo = dictamen.get("montoMaximo")
            audit.capacidad_disponible = capacidad.get("capacidadDisponible")
        
        total_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        
        audit_id = None
        if audit and db:
            audit.latency_total_ms = total_ms
            audit.status = "SUCCESS"
            db.add(audit)
            db.commit()
            db.refresh(audit)
            audit_id = audit.id
        
        return ValidationResponse(
            transaction_id=txn_id, status="SUCCESS", decision=dictamen.get("decision"),
            producto=dictamen.get("producto"), monto_maximo=dictamen.get("montoMaximo"),
            plazo_maximo=dictamen.get("plazoMaximo"), capacidad_disponible=capacidad.get("capacidadDisponible"),
            resumen=(parsed.get("resumen") or "")[:300], dictamen_completo=parsed,
            latency_ms=total_ms, audit_id=audit_id
        )
    
    except HTTPException:
        raise
    except Exception as e:
        total_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        if audit and db:
            audit.latency_total_ms = total_ms
            audit.status = "ERROR"
            audit.error_message = str(e)
            db.add(audit)
            db.commit()
        return ValidationResponse(transaction_id=txn_id, status="ERROR", error=str(e), latency_ms=total_ms)
    finally:
        if db:
            db.close()


@app.get("/api/v1/audit/{transaction_id}")
def get_audit(transaction_id: str, api_key: str = Depends(verify_api_key)):
    if not SessionLocal:
        raise HTTPException(status_code=500, detail="Database not configured")
    db = SessionLocal()
    try:
        audits = db.query(CreditValidationAudit).filter(CreditValidationAudit.transaction_id == transaction_id).all()
        if not audits:
            raise HTTPException(status_code=404, detail="No records found")
        return {"transaction_id": transaction_id, "total": len(audits),
                "audits": [{"id": a.id, "decision": a.decision, "status": a.status} for a in audits]}
    finally:
        db.close()
