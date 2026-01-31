"""
KALA Credit Validation AI Agent - Vercel Serverless
WITH VERBOSE LOGGING FOR DEBUGGING
"""

import os
import json
import hashlib
import re
import logging
import sys
import traceback
from datetime import datetime, timezone, timedelta
from typing import Optional

# =============================================================================
# VERBOSE LOGGING SETUP
# =============================================================================

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("kala-credit-validation")
logger.setLevel(logging.DEBUG)

logger.info("=" * 60)
logger.info("STARTING KALA CREDIT VALIDATION API")
logger.info("=" * 60)

# =============================================================================
# CONFIGURATION WITH LOGGING
# =============================================================================

KALA_API_BASE = os.getenv("KALA_API_BASE", "https://api.kalaplatform.tech")
KALA_AUTH_EMAIL = os.getenv("KALA_AUTH_EMAIL", "")
KALA_AUTH_PASSWORD = os.getenv("KALA_AUTH_PASSWORD", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
API_KEY_SECRET = os.getenv("API_KEY_SECRET", "kala-credit-validation-api-key-2024")

logger.info("ENVIRONMENT VARIABLES CHECK:")
logger.info(f"  KALA_API_BASE: {KALA_API_BASE}")
logger.info(f"  KALA_AUTH_EMAIL: {'SET (' + KALA_AUTH_EMAIL[:10] + '...)' if KALA_AUTH_EMAIL else 'NOT SET'}")
logger.info(f"  KALA_AUTH_PASSWORD: {'SET (length=' + str(len(KALA_AUTH_PASSWORD)) + ')' if KALA_AUTH_PASSWORD else 'NOT SET'}")
logger.info(f"  DATABASE_URL: {'SET (' + DATABASE_URL[:30] + '...)' if DATABASE_URL else 'NOT SET'}")
logger.info(f"  ANTHROPIC_API_KEY: {'SET (' + ANTHROPIC_API_KEY[:15] + '...)' if ANTHROPIC_API_KEY else 'NOT SET'}")
logger.info(f"  CLAUDE_MODEL: {CLAUDE_MODEL}")
logger.info(f"  API_KEY_SECRET: {'SET (length=' + str(len(API_KEY_SECRET)) + ')' if API_KEY_SECRET else 'NOT SET'}")

MAX_CLAUDE_RETRIES = 2
PROMPT_VERSION = "1.0.0"

# =============================================================================
# IMPORTS WITH LOGGING
# =============================================================================

logger.info("Importing dependencies...")

try:
    import httpx
    logger.info("  ✓ httpx imported")
except ImportError as e:
    logger.error(f"  ✗ httpx import failed: {e}")

try:
    import anthropic
    logger.info("  ✓ anthropic imported")
except ImportError as e:
    logger.error(f"  ✗ anthropic import failed: {e}")

try:
    from fastapi import FastAPI, HTTPException, Depends
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.security import APIKeyHeader
    logger.info("  ✓ fastapi imported")
except ImportError as e:
    logger.error(f"  ✗ fastapi import failed: {e}")

try:
    from pydantic import BaseModel, Field
    logger.info("  ✓ pydantic imported")
except ImportError as e:
    logger.error(f"  ✗ pydantic import failed: {e}")

# =============================================================================
# DATABASE SETUP WITH VERBOSE LOGGING
# =============================================================================

engine = None
SessionLocal = None
CreditValidationAudit = None
Base = None

logger.info("=" * 60)
logger.info("DATABASE CONFIGURATION")
logger.info("=" * 60)

if not DATABASE_URL:
    logger.warning("DATABASE_URL is empty - database will be disabled")
elif not DATABASE_URL.startswith("postgresql"):
    logger.error(f"DATABASE_URL has invalid format. Must start with 'postgresql://'")
    logger.error(f"Current value starts with: {DATABASE_URL[:20]}...")
else:
    logger.info(f"DATABASE_URL format looks correct (starts with 'postgresql')")
    logger.info(f"Attempting database connection...")
    
    try:
        from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, Boolean, JSON
        from sqlalchemy.orm import declarative_base, sessionmaker
        logger.info("  ✓ sqlalchemy imported")
        
        Base = declarative_base()
        logger.info("  ✓ Base created")
        
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
        
        logger.info("  ✓ CreditValidationAudit model defined")
        
        db_url = DATABASE_URL
        if "sslmode" not in db_url:
            db_url = f"{db_url}?sslmode=require"
            logger.info(f"  Added sslmode=require to connection string")
        
        logger.info(f"  Creating engine...")
        engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=300, echo=True)
        logger.info("  ✓ Engine created")
        
        logger.info(f"  Creating sessionmaker...")
        SessionLocal = sessionmaker(bind=engine)
        logger.info("  ✓ SessionLocal created")
        
        logger.info(f"  Creating tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("  ✓ Tables created/verified")
        
        # Test connection
        logger.info(f"  Testing connection...")
        with engine.connect() as conn:
            result = conn.execute("SELECT 1")
            logger.info("  ✓ Database connection test SUCCESSFUL")
        
        logger.info("=" * 60)
        logger.info("DATABASE CONFIGURED SUCCESSFULLY")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error("=" * 60)
        logger.error("DATABASE CONFIGURATION FAILED")
        logger.error("=" * 60)
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        logger.error(f"Traceback:\n{traceback.format_exc()}")
        engine = None
        SessionLocal = None
        CreditValidationAudit = None


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
    database_url_set: bool
    database_url_format: str
    claude_api: str
    claude_api_format: str
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
        logger.info(f"Token cached, expires in {expires_in}s")
    
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
            logger.debug("Using cached token")
            return token
        
        logger.info(f"Authenticating with Kala API...")
        logger.debug(f"Auth URL: {self.base_url}/v2/auth")
        
        response = client.post(
            f"{self.base_url}/v2/auth",
            json={"email": KALA_AUTH_EMAIL, "password": KALA_AUTH_PASSWORD},
            headers=self.headers
        )
        
        logger.debug(f"Auth response status: {response.status_code}")
        
        response.raise_for_status()
        data = response.json()
        
        token = data.get("token")
        if not token:
            logger.error("No token in auth response")
            raise HTTPException(status_code=500, detail="Failed to get Kala API token")
        
        TokenCache.set_token(token, data.get("expiresIn", 3600))
        logger.info("✓ Kala API authentication successful")
        return token
    
    def get_transaction_data(self, transaction_id: str) -> dict:
        logger.info(f"Getting transaction data for: {transaction_id}")
        start_time = datetime.now(timezone.utc)
        
        with httpx.Client(timeout=60.0) as client:
            token = self._ensure_token(client)
            auth_headers = {**self.headers, "Authorization": f"Bearer {token}"}
            
            # Tasks
            logger.debug(f"Fetching tasks...")
            tasks_resp = client.get(
                f"{self.base_url}/v2/task_inbox",
                params={"transactionId": transaction_id, "namesFrom": "TRUORA, BURO, GENERAL"},
                headers=auth_headers
            )
            logger.debug(f"Tasks response: {tasks_resp.status_code}")
            tasks_resp.raise_for_status()
            tasks_data = tasks_resp.json()
            logger.info(f"✓ Tasks fetched: {len(tasks_data) if isinstance(tasks_data, list) else 'N/A'} items")
            
            # Person
            logger.debug(f"Fetching person...")
            person_resp = client.get(
                f"{self.base_url}/v2/person/transaction/{transaction_id}/applicant",
                headers=auth_headers
            )
            logger.debug(f"Person response: {person_resp.status_code}")
            person_resp.raise_for_status()
            person_id = person_resp.json().get("id")
            
            if not person_id:
                logger.error(f"No person_id found for transaction {transaction_id}")
                raise HTTPException(status_code=404, detail=f"Person not found for transaction {transaction_id}")
            logger.info(f"✓ Person ID: {person_id}")
            
            # External data
            logger.debug(f"Fetching external data...")
            extdata_resp = client.get(
                f"{self.base_url}/external_data/person/{person_id}",
                headers=auth_headers
            )
            logger.debug(f"External data response: {extdata_resp.status_code}")
            extdata_resp.raise_for_status()
            extdata = extdata_resp.json()
        
        elapsed_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
        logger.info(f"✓ Kala API calls completed in {elapsed_ms}ms")
        
        ocr = extdata.get("summaryTrebolOcr")
        buro = extdata.get("customSummaryBuro")
        truora = extdata.get("summaryTruoraBackgroundChecks")
        
        logger.info(f"  OCR data: {'present' if ocr else 'MISSING'}")
        logger.info(f"  Buró data: {'present' if buro else 'MISSING'}")
        logger.info(f"  Truora data: {'present' if truora else 'MISSING'}")
        
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
    logger.debug("Consolidating data for Claude...")
    
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
    
    logger.debug(f"  Pagaduría: {pagaduria} (type: {pag_type})")
    
    deductions = salary.get("deduction_details", [])
    libranzas_ocr, embargos_ocr = [], []
    
    for d in deductions:
        desc = (d.get("description") or "").upper()
        if any(k in desc for k in ["LIBRANZA", "PRESTAMO", "CREDITO", "BCO", "BANCO"]):
            libranzas_ocr.append({"descripcion": d.get("description"), "monto": d.get("amount")})
        if "EMBARGO" in desc:
            embargos_ocr.append({"descripcion": d.get("description"), "monto": d.get("amount")})
    
    logger.debug(f"  Libranzas OCR: {len(libranzas_ocr)}, Embargos: {len(embargos_ocr)}")
    
    loans_buro = []
    for loan in buro.get("outstandingLoans", []):
        acc = loan.get("accounts", {})
        loans_buro.append({
            "entity": acc.get("lenderName"), "type": acc.get("accountType"),
            "debt": int(acc.get("totalDebt") or 0), "installment": int(acc.get("installments") or 0),
            "isLibranza": acc.get("typePayrollDeductionLoan"), "pastDueMax": acc.get("pastDueMax"),
            "paymentBehavior12m": (acc.get("paymentBehavior") or "")[:12], "sector": acc.get("sector")
        })
    
    logger.debug(f"  Loans Buró: {len(loans_buro)}")
    
    enrichment = truora.get("enrichment", {})
    processes = [{"entity": p.get("entity"), "roleDefendant": p.get("roleDefendant"),
                  "processOpen": p.get("processOpen"), "processType": p.get("processType")}
                 for p in (enrichment.get("processes") or [])]
    
    logger.debug(f"  Processes Truora: {len(processes)}")
    logger.info("✓ Data consolidated")
    
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
    logger.info("Calling Claude API...")
    logger.debug(f"  Model: {CLAUDE_MODEL}")
    logger.debug(f"  API Key prefix: {ANTHROPIC_API_KEY[:15]}...")
    
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    user_prompt = f"Analiza esta solicitud:\n{json.dumps(consolidated, indent=2, ensure_ascii=False)}"
    
    metrics = {"retries": 0, "tokens_input": 0, "tokens_output": 0, "latency_ms": 0, "raw_response": None}
    
    for attempt in range(MAX_CLAUDE_RETRIES + 1):
        try:
            logger.debug(f"  Attempt {attempt + 1}/{MAX_CLAUDE_RETRIES + 1}")
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
            
            logger.info(f"✓ Claude responded in {elapsed}ms")
            logger.debug(f"  Tokens: {response.usage.input_tokens} in / {response.usage.output_tokens} out")
            
            match = re.search(r'\{[\s\S]*\}', raw)
            if match:
                parsed = json.loads(match.group())
                logger.debug(f"  Decision: {parsed.get('dictamen', {}).get('decision', 'N/A')}")
                return parsed, metrics
            
            logger.error("No JSON found in Claude response")
            raise ValueError("No JSON found")
            
        except Exception as e:
            logger.error(f"  Attempt {attempt + 1} failed: {type(e).__name__}: {e}")
            if attempt == MAX_CLAUDE_RETRIES:
                raise


# =============================================================================
# API KEY AUTH
# =============================================================================

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Depends(api_key_header)) -> str:
    if not api_key:
        logger.warning("API Key missing in request")
        raise HTTPException(status_code=401, detail="API Key required")
    if hashlib.sha256(api_key.encode()).hexdigest() != hashlib.sha256(API_KEY_SECRET.encode()).hexdigest():
        logger.warning("Invalid API Key provided")
        raise HTTPException(status_code=401, detail="Invalid API Key")
    logger.debug("API Key verified")
    return api_key


# =============================================================================
# FASTAPI APP
# =============================================================================

app = FastAPI(title="KALA Credit Validation", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

kala_client = KalaAPIClient()

logger.info("FastAPI app initialized")


@app.get("/")
def root():
    logger.info("GET / called")
    return {"message": "KALA Credit Validation API", "version": "1.0.0"}


@app.get("/health", response_model=HealthResponse)
def health_check():
    logger.info("GET /health called")
    
    # Check DATABASE_URL format
    db_url_format = "not_set"
    if DATABASE_URL:
        if DATABASE_URL.startswith("postgresql://"):
            db_url_format = "valid"
        elif DATABASE_URL.startswith("psql "):
            db_url_format = "INVALID - remove 'psql ' prefix"
        else:
            db_url_format = f"INVALID - starts with '{DATABASE_URL[:10]}...'"
    
    # Check ANTHROPIC_API_KEY format
    claude_format = "not_set"
    if ANTHROPIC_API_KEY:
        if ANTHROPIC_API_KEY.startswith("sk-ant-"):
            claude_format = "valid"
        elif ANTHROPIC_API_KEY.startswith("eyJ"):
            claude_format = "INVALID - this looks like a JWT, not an Anthropic API key"
        else:
            claude_format = f"INVALID - should start with 'sk-ant-'"
    
    response = HealthResponse(
        status="healthy" if engine else "degraded",
        version="1.0.0",
        database="configured" if engine else "NOT configured",
        database_url_set=bool(DATABASE_URL),
        database_url_format=db_url_format,
        claude_api="configured" if ANTHROPIC_API_KEY else "NOT configured",
        claude_api_format=claude_format,
        kala_api="configured" if KALA_AUTH_EMAIL else "NOT configured"
    )
    
    logger.info(f"Health response: {response.model_dump()}")
    return response


@app.post("/api/v1/validate", response_model=ValidationResponse)
def validate_credit(request: ValidationRequest, api_key: str = Depends(verify_api_key)):
    logger.info("=" * 60)
    logger.info(f"POST /api/v1/validate - Transaction: {request.transaction_id}")
    logger.info("=" * 60)
    
    start = datetime.now(timezone.utc)
    txn_id = request.transaction_id
    
    db, audit = None, None
    if SessionLocal and CreditValidationAudit:
        logger.debug("Creating database session and audit record...")
        db = SessionLocal()
        audit = CreditValidationAudit(transaction_id=txn_id, model_version=CLAUDE_MODEL, prompt_version=PROMPT_VERSION, status="PROCESSING")
    else:
        logger.warning("Database not available - audit will not be saved")
    
    try:
        # Get data from Kala
        data = kala_client.get_transaction_data(txn_id)
        
        if audit:
            audit.person_id = data["person_id"]
            audit.input_ocr = data["ocr"]
            audit.input_buro = data["buro"]
            audit.input_truora = data["truora"]
            audit.latency_kala_api_ms = data["latency_ms"]
        
        # Consolidate
        consolidated = consolidate_data(txn_id, data["ocr"], data["buro"], data["truora"], data["tasks"])
        
        # Call Claude
        if not ANTHROPIC_API_KEY:
            logger.error("ANTHROPIC_API_KEY not configured")
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
            logger.info(f"✓ Audit saved with ID: {audit_id}")
        
        logger.info("=" * 60)
        logger.info(f"VALIDATION COMPLETE - Decision: {dictamen.get('decision')} - {total_ms}ms")
        logger.info("=" * 60)
        
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
        logger.error("=" * 60)
        logger.error(f"VALIDATION FAILED: {type(e).__name__}: {e}")
        logger.error(f"Traceback:\n{traceback.format_exc()}")
        logger.error("=" * 60)
        
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
            logger.debug("Database session closed")


@app.get("/api/v1/audit/{transaction_id}")
def get_audit(transaction_id: str, api_key: str = Depends(verify_api_key)):
    logger.info(f"GET /api/v1/audit/{transaction_id}")
    
    if not SessionLocal:
        logger.error("Database not configured")
        raise HTTPException(status_code=500, detail="Database not configured")
    
    db = SessionLocal()
    try:
        audits = db.query(CreditValidationAudit).filter(CreditValidationAudit.transaction_id == transaction_id).all()
        if not audits:
            raise HTTPException(status_code=404, detail="No records found")
        logger.info(f"Found {len(audits)} audit records")
        return {"transaction_id": transaction_id, "total": len(audits),
                "audits": [{"id": a.id, "decision": a.decision, "status": a.status} for a in audits]}
    finally:
        db.close()


logger.info("=" * 60)
logger.info("API READY")
logger.info("=" * 60)
