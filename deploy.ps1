# 天枢部署脚本 — 推送到腾讯云
# 用法: powershell -File deploy.ps1

$SERVER = "ubuntu@175.27.157.139"
$REMOTE_PATH = "~/tianshu/src/tianshu"

Write-Host "=== 天枢部署 ===" -ForegroundColor Cyan

# 1. 上传改动的源文件
Write-Host "[1/4] 上传源码..." -ForegroundColor Yellow

$files = @(
    "core/service.py", "core/context_engine.py", "core/init.py",
    "core/tool_registry.py", "core/input.py", "core/config.py",
    "core/setup.py", "core/commands.py", "core/db.py",
    "core/policy_engine.py", "core/planner.py", "core/router.py",
    "gateway/server.py", "gateway/cli.py", "gateway/chat.html",
    "gateway/delivery_ledger.py",
    "renyao/orchestrator.py", "renyao/mcp_client.py",
    "renyao/skills/learn.py", "renyao/skills/manifest.py",
    "renyao/skills/web_search.py",
    "memory/service.py", "memory/provider.py",
    "tianyao/service.py", "tianyao/agent_scheduler.py",
    "sdk/trigram.py", "sdk/models.py",
    "main.py", "__init__.py"
)

foreach ($f in $files) {
    $src = "src/tianshu/$f"
    $dst = "$SERVER`:$REMOTE_PATH/$f"
    Write-Host "  $src" -ForegroundColor DarkGray
    scp $src $dst 2>$null
}

# 2. 上传配置文件
Write-Host "[2/4] 上传配置..." -ForegroundColor Yellow
scp config/soul.md ${SERVER}:~/tianshu/config/ 2>$null
scp config/providers.yaml ${SERVER}:~/tianshu/config/ 2>$null
scp config/mcp.yaml ${SERVER}:~/tianshu/config/ 2>$null
scp pyproject.toml ${SERVER}:~/tianshu/ 2>$null

# 3. 重启服务
Write-Host "[3/4] 重启服务..." -ForegroundColor Yellow
ssh $SERVER "cd ~/tianshu && source .venv/bin/activate && pip install -e . -q && pkill -f uvicorn; sleep 2; nohup tianshu-server --port 8720 > /dev/null 2>&1 &"

# 4. 验证
Write-Host "[4/4] 验证..." -ForegroundColor Yellow
Start-Sleep -Seconds 3
try {
    $r = Invoke-WebRequest -Uri "http://175.27.157.139:8720/health" -TimeoutSec 5
    Write-Host "  部署成功! 健康检查: $($r.Content)" -ForegroundColor Green
} catch {
    Write-Host "  健康检查失败, 请手动验证" -ForegroundColor Red
}
