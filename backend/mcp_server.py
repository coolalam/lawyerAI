"""Standard MCP Streamable HTTP, with revocable read-only credentials."""
import logging
import os
import secrets
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .case_search import CaseQuery, query_cases
from .db import audit, db, now, uid
from .security import current_user, expiry, token_hash

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/mcp', tags=['MCP'])


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int = Field(30, ge=1, le=90)


@router.post('/tokens', status_code=201)
def create_token(data: TokenRequest, user=Depends(current_user)):
    name = data.name.strip()
    if not name: raise HTTPException(422,'请填写令牌名称')
    token = 'law_mcp_' + secrets.token_urlsafe(40)
    identifier, timestamp, expires = uid(), now(), expiry(data.expires_in_days*24)
    with db() as conn:
        conn.execute('INSERT INTO mcp_tokens(id,token_hash,user_id,name,created_at,expires_at) VALUES(?,?,?,?,?,?)',
                     (identifier,token_hash(token),user['id'],name,timestamp,expires))
        audit(conn,user,'创建 MCP 只读令牌','mcp_tokens',identifier,name)
    return {'id':identifier,'name':name,'token':token,'expires_at':expires,'scope':'cases:read',
            'notice':'令牌仅显示一次；仅用于 MCP，继承当前账号案件权限。'}


@router.get('/tokens')
def list_tokens(user=Depends(current_user)):
    with db() as conn:
        return {'items':[dict(r) for r in conn.execute('SELECT id,name,created_at,expires_at,revoked_at FROM mcp_tokens WHERE user_id=? ORDER BY created_at DESC',(user['id'],))]}


@router.delete('/tokens/{token_id}')
def revoke_token(token_id: str, user=Depends(current_user)):
    with db() as conn:
        row = conn.execute('SELECT user_id FROM mcp_tokens WHERE id=?',(token_id,)).fetchone()
        if not row or (row['user_id']!=user['id'] and user['role']!='admin'):
            raise HTTPException(404,'令牌不存在')
        conn.execute('UPDATE mcp_tokens SET revoked_at=? WHERE id=?',(now(),token_id))
        audit(conn,user,'撤销 MCP 令牌','mcp_tokens',token_id)
    return {'ok':True}


def authenticate(token):
    with db() as conn:
        row = conn.execute('''SELECT u.* FROM users u JOIN mcp_tokens t ON t.user_id=u.id
            WHERE t.token_hash=? AND t.expires_at>? AND t.revoked_at IS NULL AND u.active=1''',(token_hash(token),now())).fetchone()
        return dict(row) if row else None


class MCPGateway:
    transport = None

    async def __call__(self, scope, receive, send):
        request = Request(scope)
        scheme, _, token = request.headers.get('authorization','').partition(' ')
        user = await to_thread.run_sync(authenticate, token) if scheme.lower()=='bearer' and token else None
        if not user:
            response = JSONResponse({'detail':'需要有效的 MCP Bearer 令牌'},status_code=401,headers={'WWW-Authenticate':'Bearer realm="law-mcp"','Cache-Control':'no-store'})
            return await response(scope,receive,send)
        scope['law_mcp_user'] = user
        if self.transport is None:
            return await JSONResponse({'detail':'MCP 服务尚未就绪'},status_code=503)(scope,receive,send)
        # A mount at /mcp strips the path to an empty string for the exact URL.
        # The MCP app exposes its Streamable HTTP route at '/', so normalize both
        # /mcp and /mcp/ to the same endpoint.
        if scope.get('path') in ('', '/mcp'):
            scope = dict(scope)
            scope['path'] = '/'
        await self.transport(scope,receive,send)


gateway = MCPGateway()


def transport_security() -> TransportSecuritySettings:
    """Build MCP DNS-rebinding protection from the public server settings.

    ``LAW_ALLOWED_ORIGINS`` is the browser-facing configuration, while the
    MCP transport validates the Host header separately.  Deriving hosts from
    those origins keeps a reverse-proxied public URL usable without weakening
    the default host allowlist.
    """
    local_hosts = {
        '127.0.0.1', '127.0.0.1:*',
        'localhost', 'localhost:*',
        '[::1]', '[::1]:*',
        'testserver',
    }
    configured_hosts = {
        value.strip() for value in os.environ.get('LAW_MCP_ALLOWED_HOSTS', '').split(',')
        if value.strip()
    }
    allowed_hosts = local_hosts | configured_hosts

    allowed_origins = {
        'http://127.0.0.1:*',
        'http://localhost:*',
        'http://[::1]:*',
    }
    for value in os.environ.get('LAW_ALLOWED_ORIGINS', '').split(','):
        origin = value.strip().rstrip('/')
        if not origin:
            continue
        allowed_origins.add(origin)
        parsed = urlparse(origin)
        if parsed.scheme in ('http', 'https') and parsed.netloc:
            allowed_hosts.add(parsed.netloc)

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(allowed_hosts),
        allowed_origins=sorted(allowed_origins),
    )


@asynccontextmanager
async def mcp_lifespan():
    server = FastMCP('律所案件查询',stateless_http=True,json_response=True,streamable_http_path='/',
                     instructions='只读案件查询。至少填写一个条件，多个条件按 AND。先确认查询范围，按 has_more 分页读取。不要将未关联的历史记录视为不存在。',
                     transport_security=transport_security())

    @server.tool(name='query_cases',annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
    async def search_cases(filters: CaseQuery, ctx: Context) -> dict:
        """查询案件及全部已关联明细。filters 至少含一个非空查询条件，所有条件 AND。

        支持案件名称、客户名称、案件类型、主办律师姓名或 ID、分支机构名称、
        收案日期起止、对方当事人、案件状态。日期区间含首尾，可只填一端。
        返回 case、lead_lawyer、client、finance（汇总/开票/到账/计划/分配）、
        seals、tasks（日程与待办）、contracts；明细保留历史字段和附件位置。
        客户源表按机构及客户编号关联，案件总数和明细均遵守调用者权限。
        page 从 1 开始，page_size 为 1–50；无匹配返回空 items。
        """
        request = ctx.request_context.request
        user = request.scope.get('law_mcp_user') if request else None
        if not user: raise ToolError('调用身份无效')
        try:
            return await to_thread.run_sync(query_cases,filters,user)
        except HTTPException as exc:
            raise ToolError(str(exc.detail)) from None
        except Exception:
            logger.exception('MCP case query failed')
            raise ToolError('案件查询暂时失败，请稍后重试或联系管理员') from None

    gateway.transport = server.streamable_http_app()
    async with server.session_manager.run():
        try:
            yield
        finally:
            gateway.transport = None
