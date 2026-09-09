"""FastAPI application factory for identity and tenant context."""

import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from tradingagents.platform.api.compliance import ComplianceServicePort
from tradingagents.platform.api.connections import BrokerConnectionServicePort
from tradingagents.platform.api.identity import (
    AuthenticatedSession,
    CommandContext,
    IdentityError,
    IdentityPrincipal,
    IdentityServicePort,
)
from tradingagents.platform.api.jobs import AnalysisJobServicePort
from tradingagents.platform.api.llm_connections import LlmConnectionServicePort
from tradingagents.platform.api.portfolio import PortfolioServicePort
from tradingagents.platform.api.schemas import (
    AnalysisJobCreatedResponse,
    AnalysisJobDetailResponse,
    AnalysisJobRequest,
    AnalysisJobResponse,
    BrokerConnectionRequest,
    BrokerConnectionResponse,
    BrokerDiagnosticResponse,
    ConsentRequest,
    ConsentResponse,
    CsrfResponse,
    ExchangeCatalogResponse,
    HealthResponse,
    LlmConnectionRequest,
    LlmConnectionResponse,
    LlmProviderResponse,
    LoginRequest,
    MandateRequest,
    MandateResponse,
    MandateTransitionRequest,
    MessageResponse,
    MfaActivateRequest,
    MfaActivationResponse,
    MfaEnrollmentResponse,
    MfaEnrollRequest,
    MfaVerificationResponse,
    MfaVerifyRequest,
    PortfolioCreatedResponse,
    PortfolioCreateRequest,
    PortfolioFundingRequest,
    PortfolioOverviewResponse,
    PrincipalResponse,
    PrivacyActionRequest,
    PrivacyResponse,
    RecoveryRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SuitabilityRequest,
    SuitabilityResponse,
    SupabaseExchangeRequest,
    TermsResponse,
    VerificationTokenRequest,
)
from tradingagents.platform.api.security import (
    SESSION_COOKIE,
    ApiSecuritySettings,
    RateLimiter,
    RateLimitUnavailable,
    RequestBodyLimitMiddleware,
    clear_session_cookie,
    client_ip,
    issue_csrf_token,
    rate_key,
    set_csrf_cookie,
    set_session_cookie,
    verify_csrf,
)


@dataclass(frozen=True, slots=True)
class TenantRequestContext:
    principal: IdentityPrincipal
    tenant_id: UUID


def create_platform_app(
    *,
    identity_service: IdentityServicePort,
    security: ApiSecuritySettings,
    rate_limiter: RateLimiter,
    compliance_service: ComplianceServicePort | None = None,
    broker_connection_service: BrokerConnectionServicePort | None = None,
    analysis_job_service: AnalysisJobServicePort | None = None,
    llm_connection_service: LlmConnectionServicePort | None = None,
    portfolio_service: PortfolioServicePort | None = None,
) -> FastAPI:
    """Create an isolated API instance with explicit security dependencies."""

    app = FastAPI(
        title="TradingAgents DZ23",
        version="2",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=security.max_request_body_bytes)
    if security.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(security.allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-CSRF-Token",
                "X-Tenant-ID",
                "Idempotency-Key",
            ],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["cache-control"] = "no-store"
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["permissions-policy"] = "camera=(), microphone=(), geolocation=()"
        if security.strict_transport_security:
            response.headers["strict-transport-security"] = "max-age=63072000; includeSubDomains"
        return response

    def command_context(request: Request) -> CommandContext:
        raw_user_agent = request.headers.get("user-agent")
        user_agent = (
            "".join(character for character in raw_user_agent if character.isprintable())[:512]
            if raw_user_agent
            else None
        )
        return CommandContext(
            request_id=secrets.token_hex(16),
            client_ip=client_ip(request, security.trusted_proxy_networks),
            user_agent=user_agent,
        )

    async def require_session(request: Request) -> TenantRequestContext:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise _public_error(401, "AUTH_REQUIRED", "Entre na sua conta para continuar.")
        try:
            principal = await identity_service.resolve_session(
                token=token, context=command_context(request)
            )
        except IdentityError:
            raise _public_error(
                401, "SESSION_INVALID", "Sua sessão expirou. Entre novamente."
            ) from None
        requested_tenant = request.headers.get("x-tenant-id")
        if requested_tenant:
            try:
                requested_tenant_id = UUID(requested_tenant)
            except ValueError:
                raise _public_error(
                    400, "TENANT_INVALID", "O contexto da organização é inválido."
                ) from None
            if not secrets.compare_digest(str(requested_tenant_id), str(principal.tenant_id)):
                raise _public_error(
                    403,
                    "TENANT_FORBIDDEN",
                    "Você não tem acesso a esta organização.",
                )
        return TenantRequestContext(principal=principal, tenant_id=principal.tenant_id)

    def require_verified(context: TenantRequestContext) -> None:
        if not context.principal.email_verified:
            raise _public_error(
                403,
                "EMAIL_VERIFICATION_REQUIRED",
                "Confirme seu e-mail antes de continuar.",
            )

    def compliance() -> ComplianceServicePort:
        if compliance_service is None:
            raise _public_error(
                503,
                "COMPLIANCE_UNAVAILABLE",
                "Este recurso está temporariamente indisponível.",
            )
        return compliance_service

    def broker_connections() -> BrokerConnectionServicePort:
        if broker_connection_service is None:
            raise _public_error(
                503,
                "BROKER_CONNECTIONS_UNAVAILABLE",
                "As conexões com corretoras estão temporariamente indisponíveis.",
            )
        return broker_connection_service

    def analysis_jobs() -> AnalysisJobServicePort:
        if analysis_job_service is None:
            raise _public_error(
                503,
                "ANALYSIS_JOBS_UNAVAILABLE",
                "As análises estão temporariamente indisponíveis.",
            )
        return analysis_job_service

    def llm_connections() -> LlmConnectionServicePort:
        if llm_connection_service is None:
            raise _public_error(
                503,
                "LLM_CONNECTIONS_UNAVAILABLE",
                "Os provedores de inteligência artificial estão indisponíveis.",
            )
        return llm_connection_service

    def portfolios() -> PortfolioServicePort:
        if portfolio_service is None:
            raise _public_error(
                503,
                "PORTFOLIOS_UNAVAILABLE",
                "As carteiras estão temporariamente indisponíveis.",
            )
        return portfolio_service

    async def enforce_rate(
        *, request: Request, scope: str, identity: str, limit: int, window: int
    ) -> None:
        ip = client_ip(request, security.trusted_proxy_networks)
        await enforce_rate_key(key=rate_key(scope, identity, ip), limit=limit, window=window)

    async def enforce_rate_key(*, key: str, limit: int, window: int) -> None:
        try:
            allowed = await rate_limiter.allow(key=key, limit=limit, window_seconds=window)
        except RateLimitUnavailable:
            raise _public_error(
                503,
                "RATE_LIMIT_UNAVAILABLE",
                "A autenticação está temporariamente indisponível.",
            ) from None
        if not allowed:
            raise _public_error(
                429,
                "RATE_LIMITED",
                "Muitas tentativas. Aguarde alguns minutos antes de tentar novamente.",
            )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "INVALID_REQUEST",
                    "message": "Revise os campos informados e tente novamente.",
                }
            },
        )

    @app.get("/health/live", response_model=HealthResponse)
    async def live() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/health/ready", response_model=HealthResponse)
    async def ready() -> HealthResponse:
        try:
            available = await identity_service.ready()
        except Exception:
            available = False
        if not available:
            raise _public_error(
                503,
                "SERVICE_NOT_READY",
                "O serviço está inicializando. Tente novamente em instantes.",
            )
        return HealthResponse(status="ready")

    @app.get("/v1/auth/csrf", response_model=CsrfResponse)
    async def csrf(response: Response) -> CsrfResponse:
        token = issue_csrf_token(security.csrf_secret)
        set_csrf_cookie(response, token)
        return CsrfResponse(csrf_token=token)

    @app.post("/v1/auth/register", response_model=PrincipalResponse, status_code=201)
    async def register(
        payload: RegisterRequest, request: Request, response: Response
    ) -> PrincipalResponse:
        verify_csrf(request, security.csrf_secret)
        await enforce_rate(
            request=request,
            scope="register",
            identity=payload.email,
            limit=3,
            window=60 * 60,
        )
        try:
            session = await identity_service.register(
                email=payload.email,
                password=payload.password,
                display_name=payload.display_name,
                organization_name=payload.organization_name,
                organization_slug=payload.organization_slug,
                context=command_context(request),
            )
        except IdentityError as exc:
            _raise_identity_error(exc)
        _set_authenticated_session(response, session, security)
        return _principal_response(session.principal)

    @app.post("/v1/auth/login", response_model=PrincipalResponse)
    async def login(
        payload: LoginRequest, request: Request, response: Response
    ) -> PrincipalResponse:
        verify_csrf(request, security.csrf_secret)
        await enforce_rate(
            request=request,
            scope="login",
            identity=payload.email,
            limit=5,
            window=15 * 60,
        )
        try:
            session = await identity_service.authenticate(
                email=payload.email,
                password=payload.password,
                organization_slug=payload.organization_slug,
                context=command_context(request),
            )
        except IdentityError:
            raise _public_error(401, "INVALID_CREDENTIALS", "E-mail ou senha incorretos.") from None
        _set_authenticated_session(response, session, security)
        return _principal_response(session.principal)

    @app.post("/v1/auth/supabase/exchange", response_model=PrincipalResponse)
    async def exchange_supabase(
        payload: SupabaseExchangeRequest,
        request: Request,
        response: Response,
    ) -> PrincipalResponse:
        verify_csrf(request, security.csrf_secret)
        authorization = request.headers.get("authorization", "")
        scheme, separator, access_token = authorization.partition(" ")
        if (
            not separator
            or not secrets.compare_digest(scheme.casefold(), "bearer")
            or len(access_token) < 32
            or len(access_token) > 16_384
            or any(character.isspace() for character in access_token)
        ):
            raise _public_error(401, "SOCIAL_AUTH_INVALID", "Login social inválido.")
        await enforce_rate(
            request=request,
            scope="supabase-exchange",
            identity=payload.organization_slug,
            limit=10,
            window=15 * 60,
        )
        # Slug-independent guard prevents attackers from multiplying the
        # external JWKS/user traffic by cycling organization names.
        await enforce_rate_key(
            key=f"supabase-exchange-ip:{client_ip(request, security.trusted_proxy_networks)}",
            limit=30,
            window=15 * 60,
        )
        try:
            session = await identity_service.exchange_supabase_token(
                access_token=access_token,
                organization_slug=payload.organization_slug,
                organization_name=payload.organization_name,
                display_name=payload.display_name,
                context=command_context(request),
            )
        except IdentityError as exc:
            if exc.code == "SOCIAL_ONBOARDING_REQUIRED":
                raise _public_error(
                    409,
                    "SOCIAL_ONBOARDING_REQUIRED",
                    "Informe seu nome e o nome da organização para concluir o primeiro acesso.",
                ) from None
            raise _public_error(401, "SOCIAL_AUTH_INVALID", "Login social inválido.") from None
        _set_authenticated_session(response, session, security)
        return _principal_response(session.principal)

    @app.post("/v1/auth/logout", response_model=MessageResponse)
    async def logout(
        request: Request,
        response: Response,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        await enforce_rate(
            request=request,
            scope="logout",
            identity=str(context.principal.user_id),
            limit=30,
            window=60,
        )
        token = request.cookies[SESSION_COOKIE]
        try:
            await identity_service.revoke_session(token=token, context=command_context(request))
        except IdentityError:
            raise _public_error(
                503,
                "LOGOUT_UNAVAILABLE",
                "Não foi possível encerrar a sessão. Tente novamente em instantes.",
            ) from None
        clear_session_cookie(response)
        return MessageResponse(message="Sessão encerrada com segurança.")

    @app.post("/v1/auth/recovery/request", response_model=MessageResponse)
    async def request_recovery(payload: RecoveryRequest, request: Request) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        await enforce_rate(
            request=request,
            scope="recovery-request",
            identity=payload.email,
            limit=3,
            window=60 * 60,
        )
        await identity_service.request_password_reset(
            email=payload.email,
            organization_slug=payload.organization_slug,
            context=command_context(request),
        )
        return MessageResponse(
            message="Se a conta existir, enviaremos as instruções de recuperação."
        )

    @app.post("/v1/auth/recovery/reset", response_model=MessageResponse)
    async def reset_password(payload: ResetPasswordRequest, request: Request) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        await enforce_rate(
            request=request,
            scope="recovery-reset",
            identity=payload.recovery_token,
            limit=5,
            window=60 * 60,
        )
        try:
            await identity_service.reset_password(
                recovery_token=payload.recovery_token,
                new_password=payload.new_password,
                context=command_context(request),
            )
        except IdentityError:
            raise _public_error(
                400,
                "RECOVERY_INVALID",
                "Este link é inválido ou expirou. Solicite um novo link.",
            ) from None
        return MessageResponse(message="Senha alterada. Entre novamente na sua conta.")

    @app.post("/v1/auth/email-verification/request", response_model=MessageResponse)
    async def request_email_verification(
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        await enforce_rate(
            request=request,
            scope="email-verification-request",
            identity=str(context.principal.user_id),
            limit=3,
            window=60 * 60,
        )
        token = request.cookies[SESSION_COOKIE]
        await identity_service.request_email_verification(
            token=token,
            principal=context.principal,
            context=command_context(request),
        )
        return MessageResponse(message="Enviamos um novo link de verificação.")

    @app.post("/v1/auth/email-verification/confirm", response_model=MessageResponse)
    async def confirm_email_verification(
        payload: VerificationTokenRequest, request: Request
    ) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        await enforce_rate(
            request=request,
            scope="email-verification-confirm",
            identity=payload.verification_token,
            limit=5,
            window=60 * 60,
        )
        try:
            await identity_service.verify_email(
                verification_token=payload.verification_token,
                context=command_context(request),
            )
        except IdentityError:
            raise _public_error(
                400,
                "VERIFICATION_INVALID",
                "Este link é inválido ou expirou. Solicite um novo link.",
            ) from None
        return MessageResponse(message="E-mail confirmado com sucesso.")

    @app.get("/v1/auth/me", response_model=PrincipalResponse)
    async def me(
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> PrincipalResponse:
        return _principal_response(context.principal)

    @app.get("/v1/legal/terms/current", response_model=TermsResponse)
    async def current_terms() -> TermsResponse:
        row = await compliance().latest_terms(document_type="terms-of-service")
        if row is None:
            raise _public_error(503, "TERMS_UNAVAILABLE", "Os termos ainda não estão disponíveis.")
        return TermsResponse(**asdict(row))

    @app.post("/v1/legal/consents", response_model=MessageResponse, status_code=201)
    async def record_consent(
        payload: ConsentRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        await enforce_rate(
            request=request,
            scope="consent",
            identity=str(context.principal.user_id),
            limit=20,
            window=60 * 60,
        )
        try:
            await compliance().record_consent(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                terms_document_id=payload.terms_document_id,
                decision=payload.decision,
                context=command_context(request),
            )
        except IdentityError:
            raise _public_error(
                400, "CONSENT_INVALID", "Não foi possível registrar a decisão."
            ) from None
        return MessageResponse(message="Sua decisão foi registrada.")

    @app.get("/v1/legal/consents", response_model=list[ConsentResponse])
    async def list_consents(
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> list[ConsentResponse]:
        rows = await compliance().list_consents(
            token=request.cookies[SESSION_COOKIE], principal=context.principal
        )
        return [ConsentResponse(**asdict(row)) for row in rows]

    @app.post("/v1/privacy/requests", response_model=PrivacyResponse, status_code=201)
    async def request_privacy_action(
        payload: PrivacyActionRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> PrivacyResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        await enforce_rate(
            request=request,
            scope="privacy-request",
            identity=str(context.principal.user_id),
            limit=5,
            window=24 * 60 * 60,
        )
        try:
            row = await compliance().request_privacy_action(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                kind=payload.kind,
            )
        except IdentityError:
            raise _public_error(
                400, "PRIVACY_INVALID", "Não foi possível abrir a solicitação."
            ) from None
        return PrivacyResponse(**asdict(row))

    @app.get("/v1/privacy/requests/{request_id}", response_model=PrivacyResponse)
    async def get_privacy_request(
        request_id: UUID,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> PrivacyResponse:
        try:
            row = await compliance().get_privacy_request(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                request_id=request_id,
            )
        except IdentityError:
            raise _public_error(404, "PRIVACY_NOT_FOUND", "Solicitação não encontrada.") from None
        return PrivacyResponse(**asdict(row))

    @app.post("/v1/mfa/enroll", response_model=MfaEnrollmentResponse)
    async def enroll_mfa(
        payload: MfaEnrollRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> MfaEnrollmentResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        try:
            row = await compliance().enroll_mfa(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                account_label=payload.account_label,
            )
        except IdentityError:
            raise _public_error(
                400, "MFA_ENROLLMENT_INVALID", "Não foi possível iniciar o MFA."
            ) from None
        return MfaEnrollmentResponse(**asdict(row))

    @app.post("/v1/mfa/activate", response_model=MfaActivationResponse)
    async def activate_mfa(
        payload: MfaActivateRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> MfaActivationResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        try:
            row = await compliance().activate_mfa(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                code=payload.code,
            )
        except IdentityError:
            raise _public_error(400, "MFA_VERIFICATION_FAILED", "Código MFA inválido.") from None
        return MfaActivationResponse(**asdict(row))

    @app.post("/v1/mfa/verify", response_model=MfaVerificationResponse)
    async def verify_mfa(
        payload: MfaVerifyRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> MfaVerificationResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        await enforce_rate(
            request=request,
            scope="mfa-verify",
            identity=str(context.principal.user_id),
            limit=5,
            window=15 * 60,
        )
        try:
            row = await compliance().verify_mfa(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                code=payload.code,
                recovery_code=payload.recovery_code,
            )
        except IdentityError:
            raise _public_error(401, "MFA_VERIFICATION_FAILED", "Código MFA inválido.") from None
        return MfaVerificationResponse(**asdict(row))

    @app.get("/v1/exchanges/catalog", response_model=list[ExchangeCatalogResponse])
    async def list_exchange_catalog(
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> list[ExchangeCatalogResponse]:
        require_verified(context)
        rows = await broker_connections().catalog()
        return [ExchangeCatalogResponse(**asdict(row)) for row in rows]

    @app.get("/v1/connections", response_model=list[BrokerConnectionResponse])
    async def list_broker_connections(
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> list[BrokerConnectionResponse]:
        require_verified(context)
        try:
            rows = await broker_connections().list_connections(
                token=request.cookies[SESSION_COOKIE], principal=context.principal
            )
        except IdentityError as exc:
            _raise_connection_error(exc)
        return [BrokerConnectionResponse(**asdict(row)) for row in rows]

    @app.post("/v1/connections", response_model=MessageResponse, status_code=201)
    async def save_broker_connection(
        payload: BrokerConnectionRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=128)
        ],
    ) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        await enforce_rate_key(
            key=f"connection-save:{context.tenant_id}:{context.principal.user_id}",
            limit=10,
            window=3600,
        )
        try:
            connection_id = await broker_connections().save_connection(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                idempotency_key=idempotency_key,
                label=payload.label,
                exchange_slug=payload.exchange_slug,
                environment=payload.environment,
                api_key=payload.api_key.get_secret_value(),
                secret=payload.secret.get_secret_value(),
                passphrase=(payload.passphrase.get_secret_value() if payload.passphrase else None),
                uid=payload.uid.get_secret_value() if payload.uid else None,
            )
        except IdentityError as exc:
            _raise_connection_error(exc)
        return MessageResponse(message=f"Conexão {connection_id} salva com segurança.")

    @app.post(
        "/v1/connections/{connection_id}/diagnose",
        response_model=BrokerDiagnosticResponse,
    )
    async def diagnose_broker_connection(
        connection_id: UUID,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> BrokerDiagnosticResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        await enforce_rate_key(
            key=f"connection-diagnostic:{context.tenant_id}:{connection_id}",
            limit=5,
            window=300,
        )
        try:
            row = await broker_connections().diagnose(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                connection_id=connection_id,
            )
        except IdentityError as exc:
            _raise_connection_error(exc)
        return BrokerDiagnosticResponse(**asdict(row))

    @app.delete("/v1/connections/{connection_id}", response_model=MessageResponse)
    async def remove_broker_connection(
        connection_id: UUID,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        await enforce_rate_key(
            key=f"connection-remove:{context.tenant_id}:{context.principal.user_id}",
            limit=10,
            window=3600,
        )
        try:
            await broker_connections().remove_connection(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                connection_id=connection_id,
            )
        except IdentityError as exc:
            _raise_connection_error(exc)
        return MessageResponse(message="Credencial removida e conexão desativada.")

    @app.post(
        "/v1/portfolio/suitability",
        response_model=SuitabilityResponse,
        status_code=201,
    )
    async def assess_suitability(
        payload: SuitabilityRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> SuitabilityResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        await enforce_rate_key(
            key=f"suitability:{context.tenant_id}:{context.principal.user_id}",
            limit=10,
            window=24 * 60 * 60,
        )
        answers = payload.model_dump(exclude={"requested_profile"})
        try:
            row = await portfolios().assess_suitability(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                requested_profile=payload.requested_profile,
                answers=answers,
            )
        except IdentityError as exc:
            _raise_portfolio_error(exc)
        return SuitabilityResponse(**row)

    @app.post("/v1/portfolios", response_model=PortfolioCreatedResponse, status_code=201)
    async def create_portfolio(
        payload: PortfolioCreateRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> PortfolioCreatedResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        try:
            row = await portfolios().create_portfolio(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                name=payload.name,
                base_currency=payload.base_currency,
            )
        except IdentityError as exc:
            _raise_portfolio_error(exc)
        return PortfolioCreatedResponse(**row)

    @app.get("/v1/portfolios", response_model=list[PortfolioOverviewResponse])
    async def list_portfolios(
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> list[PortfolioOverviewResponse]:
        require_verified(context)
        try:
            rows = await portfolios().list_portfolios(
                token=request.cookies[SESSION_COOKIE], principal=context.principal
            )
        except IdentityError as exc:
            _raise_portfolio_error(exc)
        return [PortfolioOverviewResponse(**row) for row in rows]

    @app.post(
        "/v1/portfolios/{portfolio_id}/mandates",
        response_model=MandateResponse,
        status_code=201,
    )
    async def activate_portfolio_mandate(
        portfolio_id: UUID,
        payload: MandateRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> MandateResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        try:
            row = await portfolios().activate_mandate(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                portfolio_id=portfolio_id,
                suitability_id=payload.suitability_id,
                profile=payload.profile,
                mode=payload.mode,
            )
        except IdentityError as exc:
            _raise_portfolio_error(exc)
        return MandateResponse(**row)

    @app.patch(
        "/v1/portfolios/{portfolio_id}/mandate",
        response_model=MandateResponse,
    )
    async def transition_portfolio_mandate(
        portfolio_id: UUID,
        payload: MandateTransitionRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> MandateResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        try:
            row = await portfolios().transition_mandate(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                portfolio_id=portfolio_id,
                target_status=payload.target_status,
            )
        except IdentityError as exc:
            _raise_portfolio_error(exc)
        return MandateResponse(**row)

    @app.post(
        "/v1/portfolios/{portfolio_id}/funding",
        response_model=MessageResponse,
        status_code=201,
    )
    async def record_portfolio_funding(
        portfolio_id: UUID,
        payload: PortfolioFundingRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=128)
        ],
    ) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        try:
            transaction_id = await portfolios().record_funding(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                portfolio_id=portfolio_id,
                amount=payload.amount,
                idempotency_key=idempotency_key,
                reference_id=payload.reference_id,
            )
        except IdentityError as exc:
            _raise_portfolio_error(exc)
        return MessageResponse(
            message=(
                "Saldo PAPER/SHADOW informado pelo usuário e marcado como não verificado: "
                f"{transaction_id}."
            )
        )

    @app.post("/v1/jobs", response_model=AnalysisJobCreatedResponse, status_code=202)
    async def enqueue_analysis_job(
        payload: AnalysisJobRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=128)
        ],
    ) -> AnalysisJobCreatedResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        await enforce_rate_key(
            key=f"analysis-job-create:{context.tenant_id}:{context.principal.user_id}",
            limit=30,
            window=3600,
        )
        try:
            job_id = await analysis_jobs().enqueue(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                idempotency_key=idempotency_key,
                command=payload.model_dump(mode="json"),
            )
        except IdentityError as exc:
            _raise_job_error(exc)
        return AnalysisJobCreatedResponse(
            id=job_id,
            message="Análise adicionada à fila com segurança.",
        )

    @app.get("/v1/llm/providers", response_model=list[LlmProviderResponse])
    async def list_llm_providers(
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> list[LlmProviderResponse]:
        require_verified(context)
        return [LlmProviderResponse(**asdict(row)) for row in await llm_connections().catalog()]

    @app.get("/v1/llm/connections", response_model=list[LlmConnectionResponse])
    async def list_llm_connections(
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> list[LlmConnectionResponse]:
        require_verified(context)
        try:
            rows = await llm_connections().list_connections(
                token=request.cookies[SESSION_COOKIE], principal=context.principal
            )
        except IdentityError as exc:
            _raise_llm_connection_error(exc)
        return [LlmConnectionResponse(**asdict(row)) for row in rows]

    @app.post("/v1/llm/connections", response_model=MessageResponse, status_code=201)
    async def save_llm_connection(
        payload: LlmConnectionRequest,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=16, max_length=128)
        ],
    ) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        await enforce_rate_key(
            key=f"llm-connection-save:{context.tenant_id}:{context.principal.user_id}",
            limit=10,
            window=3600,
        )
        try:
            connection_id = await llm_connections().save_connection(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                idempotency_key=idempotency_key,
                label=payload.label,
                provider=payload.provider,
                api_key=payload.api_key.get_secret_value(),
                base_url=payload.base_url,
            )
        except IdentityError as exc:
            _raise_llm_connection_error(exc)
        return MessageResponse(message=f"Provedor {connection_id} salvo com segurança.")

    @app.delete("/v1/llm/connections/{connection_id}", response_model=MessageResponse)
    async def disable_llm_connection(
        connection_id: UUID,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        try:
            await llm_connections().disable_connection(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                connection_id=connection_id,
            )
        except IdentityError as exc:
            _raise_llm_connection_error(exc)
        return MessageResponse(message="Provedor desativado.")

    @app.get("/v1/jobs", response_model=list[AnalysisJobResponse])
    async def list_analysis_jobs(
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[AnalysisJobResponse]:
        require_verified(context)
        await enforce_rate(
            request=request,
            scope="analysis-job-list",
            identity=f"{context.tenant_id}:{context.principal.user_id}",
            limit=60,
            window=60,
        )
        try:
            rows = await analysis_jobs().list_jobs(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                limit=limit,
            )
        except IdentityError as exc:
            _raise_job_error(exc)
        return [AnalysisJobResponse(**asdict(row)) for row in rows]

    @app.get("/v1/jobs/{job_id}", response_model=AnalysisJobDetailResponse)
    async def get_analysis_job(
        job_id: UUID,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> AnalysisJobDetailResponse:
        require_verified(context)
        await enforce_rate(
            request=request,
            scope="analysis-job-detail",
            identity=f"{context.tenant_id}:{context.principal.user_id}",
            limit=30,
            window=60,
        )
        try:
            detail = await analysis_jobs().get_job(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                job_id=job_id,
            )
        except IdentityError as exc:
            _raise_job_error(exc)
        return AnalysisJobDetailResponse(
            **asdict(detail.job),
            result=detail.result,
            manifest=detail.manifest,
        )

    @app.post("/v1/jobs/{job_id}/cancel", response_model=MessageResponse)
    async def cancel_analysis_job(
        job_id: UUID,
        request: Request,
        context: Annotated[TenantRequestContext, Depends(require_session)],
    ) -> MessageResponse:
        verify_csrf(request, security.csrf_secret)
        require_verified(context)
        try:
            status = await analysis_jobs().cancel(
                token=request.cookies[SESSION_COOKIE],
                principal=context.principal,
                job_id=job_id,
            )
        except IdentityError as exc:
            _raise_job_error(exc)
        return MessageResponse(message=f"Cancelamento registrado: {status}.")

    return app


def _set_authenticated_session(
    response: Response, session: AuthenticatedSession, security: ApiSecuritySettings
) -> None:
    remaining_seconds = int((session.principal.expires_at - datetime.now(UTC)).total_seconds())
    if remaining_seconds <= 0:
        raise RuntimeError("identity service returned an expired session")
    max_age = min(security.session_ttl_seconds, remaining_seconds)
    set_session_cookie(response, session.token, max_age)
    rotated_csrf = issue_csrf_token(security.csrf_secret)
    set_csrf_cookie(response, rotated_csrf)
    response.headers["x-csrf-token"] = rotated_csrf


def _principal_response(principal: IdentityPrincipal) -> PrincipalResponse:
    return PrincipalResponse(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        email=principal.email,
        display_name=principal.display_name,
        roles=principal.roles,
        email_verified=principal.email_verified,
    )


def _raise_identity_error(exc: IdentityError) -> None:
    if exc.code in {"EMAIL_EXISTS", "REGISTRATION_CONFLICT"}:
        raise _public_error(
            409,
            "ACCOUNT_OR_ORGANIZATION_EXISTS",
            "Este e-mail ou identificador da organização já está em uso.",
        ) from None
    raise _public_error(
        400, "REGISTRATION_FAILED", "Não foi possível criar a conta com esses dados."
    ) from None


def _raise_connection_error(exc: IdentityError) -> None:
    if exc.code == "MFA_STEP_UP_REQUIRED":
        raise _public_error(
            403,
            exc.code,
            "Confirme o código de segurança novamente para acessar credenciais.",
        ) from None
    if exc.code in {"CONNECTION_NOT_FOUND", "CREDENTIAL_NOT_FOUND"}:
        raise _public_error(404, "CONNECTION_NOT_FOUND", "Conexão não encontrada.") from None
    if exc.code in {"CONNECTION_FORBIDDEN"}:
        raise _public_error(403, exc.code, "Você não pode administrar conexões.") from None
    if exc.code == "TESTNET_UNAVAILABLE":
        raise _public_error(
            400,
            exc.code,
            "Esta corretora não oferece testnet real; o sistema não muda para LIVE sozinho.",
        ) from None
    raise _public_error(
        400,
        "CONNECTION_INVALID",
        "Não foi possível salvar ou diagnosticar esta conexão.",
    ) from None


def _raise_job_error(exc: IdentityError) -> None:
    if exc.code == "JOB_NOT_FOUND":
        raise _public_error(404, exc.code, "Análise não encontrada.") from None
    if exc.code in {"JOB_FORBIDDEN"}:
        raise _public_error(403, exc.code, "Você não pode administrar análises.") from None
    if exc.code in {"ACTIVE_JOB_QUOTA_EXCEEDED"}:
        raise _public_error(
            429,
            exc.code,
            "Há muitas análises em andamento. Aguarde uma conclusão.",
        ) from None
    if exc.code in {"IDEMPOTENCY_PAYLOAD_MISMATCH", "IDEMPOTENCY_KEY_INVALID"}:
        raise _public_error(
            409,
            exc.code,
            "Esta solicitação não pode reutilizar o mesmo identificador.",
        ) from None
    raise _public_error(400, "ANALYSIS_JOB_INVALID", "Não foi possível agendar a análise.")


def _raise_llm_connection_error(exc: IdentityError) -> None:
    if exc.code == "MFA_STEP_UP_REQUIRED":
        raise _public_error(
            403,
            exc.code,
            "Confirme o código de segurança para administrar este provedor.",
        ) from None
    if exc.code == "LLM_CONNECTION_NOT_FOUND":
        raise _public_error(404, exc.code, "Provedor não encontrado.") from None
    if exc.code == "LLM_CONNECTION_FORBIDDEN":
        raise _public_error(403, exc.code, "Você não pode administrar provedores.") from None
    raise _public_error(
        400,
        "LLM_CONNECTION_INVALID",
        "Não foi possível salvar este provedor. Revise os dados informados.",
    ) from None


def _raise_portfolio_error(exc: IdentityError) -> None:
    code = str(exc)
    if code in {"PORTFOLIO_NOT_FOUND", "RESERVATION_NOT_FOUND"}:
        raise _public_error(404, code, "Carteira ou registro não encontrado.") from None
    if code == "PORTFOLIO_FORBIDDEN":
        raise _public_error(403, code, "Você não pode alterar esta carteira.") from None
    if code in {"SUITABILITY_REQUIRED", "PROFILE_EXCEEDS_SUITABILITY"}:
        raise _public_error(
            409,
            code,
            "Complete o questionário de perfil antes de ativar esta configuração.",
        ) from None
    if code in {"CAPITAL_MODE_NOT_ENABLED", "CIRCUIT_BREAKER_OPEN"}:
        raise _public_error(
            409,
            code,
            "A operação foi bloqueada pelos controles de segurança da carteira.",
        ) from None
    if code == "INSUFFICIENT_AVAILABLE_CASH":
        raise _public_error(409, code, "Saldo disponível insuficiente.") from None
    if code in {"IDEMPOTENCY_PAYLOAD_MISMATCH", "PORTFOLIO_ALREADY_EXISTS"}:
        raise _public_error(409, code, "A solicitação conflita com um registro existente.") from None
    raise _public_error(
        400,
        "PORTFOLIO_INVALID",
        "Não foi possível concluir a operação da carteira.",
    ) from None


def _public_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )
