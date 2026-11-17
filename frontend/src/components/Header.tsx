import { useState } from "react";
import { BarChart3, LogOut, X, UserCircle, Terminal, Copy, Check, ExternalLink, Sun, Moon } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";
import { useColorMode } from "../contexts/ColorModeContext";
import { useNavigate } from "react-router-dom";

export const APP_VERSION = "v2.8.0";

const CHANGELOG = [
  {
    version: "v2.8.0",
    date: "2026-04-30",
    items: [
      "WQ BRAIN 提交修复：SC 轮询确认机制，提交状态与平台 ACTIVE 严格对齐",
      "新增 alpha-status / submit-by-id 端点，支持单个 alpha 状态查询和直接提交",
      "累计 8 个因子平台 ACTIVE（ts_decay_linear 3 + ts_av_diff 5）",
    ],
  },
  {
    version: "v2.7.0",
    date: "2026-04-29",
    items: [
      "WQ BRAIN 自主因子挖掘：3 轮 Session、8 轮实验，产出 21 个 A 级因子",
      "发现 ts_av_diff 第二独立算子家族，突破 SC 饱和瓶颈",
      "新增批量提交 + 并发控制 + 连接异常自动重试",
    ],
  },
  {
    version: "v2.6.0",
    date: "2026-04-29",
    items: [
      "移除 SC 本地检查，A 级因子直接提交平台判定",
      "WQ 模式放宽校验：未知算子/字段透传 BRAIN 不再本地拦截",
      "ResearchDashboard 金融量化风格重构",
    ],
  },
];

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const { isDark } = useColorMode();
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
      className={`absolute top-2 right-2 p-1 rounded text-gray-400 ${isDark ? "hover:text-gray-300 hover:bg-gray-800" : "hover:text-gray-600 hover:bg-gray-100"} transition-colors`}
      title="复制"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

function McpGuideModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<"claude" | "http">("http");
  const { isDark } = useColorMode();

  const endpoint = new URL("/mcp/", import.meta.env.DEV ? "http://localhost:8003" : window.location.origin).href;
  const listCommand = `python scripts/mcp_bridge.py --url "${endpoint}" --bearer-token-env RESEARCH_TOKEN --timeout-seconds 30 list`;
  const mcpConfig = `{
  "mcpServers": {
    "quantgpt": {
      "type": "stdio",
      "command": "/absolute/path/to/QuantGPT_o/.venv312/bin/python",
      "args": ["-m", "quantgpt", "--transport", "stdio"]
    }
  }
}`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className={`${isDark ? "bg-gray-900" : "bg-white"} rounded-2xl shadow-xl w-full max-w-lg mx-4 max-h-[85vh] flex flex-col`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={`flex items-center justify-between px-5 py-4 border-b ${isDark ? "border-gray-700" : "border-gray-100"}`}>
          <div className="flex items-center gap-2.5">
            <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-orange-500 to-amber-500 flex items-center justify-center">
              <Terminal className="h-4 w-4 text-white" />
            </div>
            <div>
              <h2 className={`text-base font-semibold ${isDark ? "text-gray-100" : "text-gray-900"}`}>MCP 集成指南</h2>
              <p className="text-xs text-gray-400">通过 MCP 协议接入 QuantGPT 回测能力</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className={`p-1.5 rounded-lg text-gray-400 ${isDark ? "hover:text-gray-300 hover:bg-gray-800" : "hover:text-gray-600 hover:bg-gray-100"} transition-colors`}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className={`px-5 pt-3 flex gap-1 border-b ${isDark ? "border-gray-700" : "border-gray-100"}`}>
          <button
            onClick={() => setTab("claude")}
            className={`px-3 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              tab === "claude"
                ? isDark
                  ? "text-orange-400 border-b-2 border-orange-500 bg-orange-500/10"
                  : "text-orange-600 border-b-2 border-orange-500 bg-orange-50/50"
                : isDark
                  ? "text-gray-400 hover:text-gray-300 hover:bg-gray-800"
                  : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
            }`}
          >
            本机 stdio
          </button>
          <button
            onClick={() => setTab("http")}
            className={`px-3 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              tab === "http"
                ? isDark
                  ? "text-amber-400 border-b-2 border-amber-500 bg-amber-500/10"
                  : "text-blue-600 border-b-2 border-blue-500 bg-blue-50/50"
                : isDark
                  ? "text-gray-400 hover:text-gray-300 hover:bg-gray-800"
                  : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
            }`}
          >
            Streamable HTTP
          </button>
        </div>

        <div className="overflow-y-auto px-5 py-4 space-y-5">
          {tab === "claude" ? (
            <>
              {/* Local stdio */}
              <div className={`${isDark ? "bg-amber-500/10" : "bg-blue-50"} rounded-lg p-3.5`}>
                <p className={`text-sm ${isDark ? "text-amber-300" : "text-blue-800"}`}>
                  本机客户端可启动项目虚拟环境中的 Python，通过标准输入输出连接 QuantGPT。以下为 Claude Code 的项目配置示例。
                </p>
              </div>

              {/* Step 1: Clone */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`h-5 w-5 rounded-full ${isDark ? "bg-gray-100" : "bg-gray-900"} ${isDark ? "text-gray-900" : "text-white"} text-xs flex items-center justify-center font-medium`}>1</span>
                  <h3 className={`text-sm font-medium ${isDark ? "text-gray-100" : "text-gray-900"}`}>克隆项目并安装</h3>
                </div>
                <div className="relative bg-gray-900 rounded-lg p-3 font-mono text-xs text-gray-100 leading-relaxed">
                  <CopyButton text={"git clone https://github.com/popura404/QuantGPT_o.git\ncd QuantGPT_o\nmake setup"} />
                  <pre className="whitespace-pre-wrap"><span className="text-green-400">$</span> git clone https://github.com/popura404/QuantGPT_o.git{"\n"}<span className="text-green-400">$</span> cd QuantGPT_o{"\n"}<span className="text-green-400">$</span> make setup</pre>
                </div>
              </div>

              {/* Step 2: Configure */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`h-5 w-5 rounded-full ${isDark ? "bg-gray-100" : "bg-gray-900"} ${isDark ? "text-gray-900" : "text-white"} text-xs flex items-center justify-center font-medium`}>2</span>
                  <h3 className={`text-sm font-medium ${isDark ? "text-gray-100" : "text-gray-900"}`}>配置 MCP</h3>
                </div>
                <p className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"} mb-1.5 font-medium`}>在项目根目录创建 <code className={`${isDark ? "bg-gray-800" : "bg-gray-100"} px-1 rounded`}>.mcp.json</code>：</p>
                <div className="relative bg-gray-900 rounded-lg p-3 font-mono text-xs text-gray-100 leading-relaxed">
                  <CopyButton text={mcpConfig} />
                  <pre className="whitespace-pre-wrap">{mcpConfig}</pre>
                </div>
                <p className="text-xs text-gray-400 mt-1.5">
                  将 command 替换为项目虚拟环境中 Python 的绝对路径，从仓库根目录启动客户端。Windows 请使用 scripts/setup.ps1 安装，并选择 .venv312/Scripts/python.exe。
                </p>
              </div>

              {/* Step 3 */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`h-5 w-5 rounded-full ${isDark ? "bg-gray-100" : "bg-gray-900"} ${isDark ? "text-gray-900" : "text-white"} text-xs flex items-center justify-center font-medium`}>3</span>
                  <h3 className={`text-sm font-medium ${isDark ? "text-gray-100" : "text-gray-900"}`}>开始使用</h3>
                </div>
                <div className="relative bg-gray-900 rounded-lg p-3 font-mono text-sm text-gray-100">
                  <CopyButton text="claude mcp list" />
                  <div>
                    <span className="text-green-400">$</span> claude mcp list<br/>
                    <span className="text-gray-400"># quantgpt: Connected</span>
                  </div>
                </div>
                <p className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"} mt-2`}>
                  验证连接后，Agent 可自主调用工具进行因子研究：回测、评分、诊断、反过拟合检测
                </p>
              </div>
            </>
          ) : (
            <>
              <div>
                <h3 className="text-sm font-medium mb-2">服务地址</h3>
                <p className={`text-xs ${isDark ? "text-gray-400" : "text-gray-600"} mb-2`}>
                  在 MCP 客户端选择 Streamable HTTP 并填写以下地址。
                </p>
                <div className="relative bg-gray-900 rounded-lg p-3 pr-9 font-mono text-xs text-gray-100">
                  <CopyButton text={endpoint} />
                  <code className="break-all">{endpoint}</code>
                </div>
              </div>
              <div>
                <h3 className="text-sm font-medium mb-2">请求认证</h3>
                <p className={`text-xs ${isDark ? "text-gray-400" : "text-gray-600"}`}>
                  启用认证时，在客户端添加 <code>Authorization: Bearer &lt;token&gt;</code> 请求头。
                  使用自己的 QuantGPT API key 或登录 JWT。管理员也可以提供与运行中服务的 <code>QUANTGPT_MCP_HTTP_TOKEN</code> 一致的部署令牌。
                </p>
              </div>
              <div>
                <h3 className="text-sm font-medium mb-2">验证连接</h3>
                <p className={`text-xs ${isDark ? "text-gray-400" : "text-gray-600"} mb-2`}>
                  在本机环境变量 RESEARCH_TOKEN 中设置令牌，再从仓库根目录用项目虚拟环境运行以下命令。
                  它会初始化 MCP 会话并列出工具。若服务已关闭认证，可省略 <code>--bearer-token-env RESEARCH_TOKEN</code>。
                </p>
                <div className="relative bg-gray-900 rounded-lg p-3 pr-9 font-mono text-xs text-gray-100">
                  <CopyButton text={listCommand} />
                  <pre className="whitespace-pre-wrap break-all">{listCommand}</pre>
                </div>
              </div>
            </>
          )}

          {/* Available tools - shared */}
          <div>
            <h3 className={`text-sm font-medium ${isDark ? "text-gray-100" : "text-gray-900"} mb-2`}>工具示例</h3>
            <div className="grid grid-cols-2 gap-1.5">
              {[
                { name: "list_operators", desc: "查看算子列表" },
                { name: "list_universes", desc: "查看股票池" },
                { name: "validate_expression", desc: "验证表达式" },
                { name: "run_backtest", desc: "执行回测" },
                { name: "score_factor", desc: "因子评分" },
                { name: "diagnose_factor", desc: "诊断因子" },
                { name: "run_anti_overfit", desc: "抗过拟合检测" },
                { name: "run_rolling_validation", desc: "滚动验证" },
              ].map((tool) => (
                <div key={tool.name} className={`flex items-center gap-2 px-2.5 py-1.5 rounded-md ${isDark ? "bg-gray-800" : "bg-gray-50"}`}>
                  <code className="text-xs text-orange-600 font-medium">{tool.name}</code>
                  <span className="text-xs text-gray-400">{tool.desc}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className={`px-5 py-3 border-t ${isDark ? "border-gray-700" : "border-gray-100"} flex items-center justify-between`}>
          <a
            href="https://github.com/popura404/QuantGPT_o/blob/main/docs/agent_safe_workflow.md"
            target="_blank"
            rel="noopener noreferrer"
            className={`flex items-center gap-1.5 text-sm ${isDark ? "text-gray-400 hover:text-gray-300" : "text-gray-500 hover:text-gray-700"} transition-colors`}
          >
            <ExternalLink className="h-3.5 w-3.5" />
            多代理研究操作指南
          </a>
          <button
            onClick={onClose}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium ${isDark ? "text-gray-400 hover:bg-gray-800" : "text-gray-600 hover:bg-gray-100"} transition-colors`}
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

function ColorModeToggle() {
  const { colorMode, toggleColorMode, isDark } = useColorMode();
  return (
    <button
      onClick={toggleColorMode}
      className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium border ${isDark ? "border-gray-700 hover:bg-gray-800" : "border-gray-200 hover:bg-gray-50"} transition-colors`}
      title={colorMode === "cn" ? "当前：红涨绿跌（中国）" : "当前：绿涨红跌（西方）"}
    >
      <span className={colorMode === "cn" ? "text-red-500" : "text-emerald-500"}>涨</span>
      <span className="text-gray-300">/</span>
      <span className={colorMode === "cn" ? "text-emerald-500" : "text-red-500"}>跌</span>
    </button>
  );
}

function DarkModeToggle() {
  const { isDark, toggleDark } = useColorMode();
  return (
    <button
      onClick={toggleDark}
      className={`p-1.5 rounded-md border ${isDark ? "border-gray-700 hover:bg-gray-800" : "border-gray-200 hover:bg-gray-50"} transition-colors`}
      title={isDark ? "切换到浅色模式" : "切换到深色模式"}
    >
      {isDark ? <Sun className="h-3.5 w-3.5 text-amber-400" /> : <Moon className="h-3.5 w-3.5 text-gray-500" />}
    </button>
  );
}

export default function Header() {
  const { user, isGuest, logout } = useAuth();
  const { isDark } = useColorMode();
  const navigate = useNavigate();
  const [showChangelog, setShowChangelog] = useState(false);
  const [showMcpGuide, setShowMcpGuide] = useState(false);

  return (
    <>
      <header className={`border-b ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-200 bg-white"}`}>
        <div className="mx-auto max-w-7xl px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <BarChart3 className={`h-6 w-6 ${isDark ? "text-amber-400" : "text-blue-600"}`} />
            <div>
              <div className="flex items-center gap-2">
                <h1 className={`text-lg font-semibold ${isDark ? "text-gray-100" : "text-gray-900"}`}>QuantGPT</h1>
                <button
                  onClick={() => setShowChangelog(true)}
                  className={`text-xs px-1.5 py-0.5 rounded ${isDark ? "bg-amber-500/10 text-amber-400 hover:bg-amber-500/20" : "bg-blue-50 text-blue-600 hover:bg-blue-100"} transition-colors font-mono`}
                >
                  {APP_VERSION}
                </button>
              </div>
              <p className={`text-sm ${isDark ? "text-gray-400" : "text-gray-500"}`}>Agent 驱动的 LLM 量化研究引擎</p>
            </div>
          </div>
          {isGuest ? (
            <div className="flex items-center gap-3">
              <DarkModeToggle />
              <ColorModeToggle />
              <button
                onClick={() => setShowMcpGuide(true)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm text-orange-600 hover:bg-orange-50 transition-colors"
                title="MCP 集成指南"
              >
                <Terminal className="h-4 w-4" />
                <span className="hidden sm:inline">MCP</span>
              </button>
              <span className="flex items-center gap-1.5 text-sm text-amber-600">
                <UserCircle className="h-4 w-4" />
                游客模式
              </span>
              <button
                onClick={() => { logout(); navigate("/login"); }}
                className="px-3 py-1.5 rounded-lg text-sm font-medium text-blue-600 hover:bg-blue-50 transition-colors"
              >
                注册 / 登录
              </button>
            </div>
          ) : user ? (
            <div className="flex items-center gap-3">
              <DarkModeToggle />
              <ColorModeToggle />
              <button
                onClick={() => setShowMcpGuide(true)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm text-orange-600 hover:bg-orange-50 transition-colors"
                title="MCP 集成指南"
              >
                <Terminal className="h-4 w-4" />
                <span className="hidden sm:inline">MCP</span>
              </button>
              <span className={`text-sm ${isDark ? "text-gray-400" : "text-gray-600"}`}>{user.email}</span>
              <button
                onClick={logout}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm ${isDark ? "text-gray-400 hover:text-gray-300 hover:bg-gray-800" : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"} transition-colors`}
              >
                <LogOut className="h-4 w-4" />
                退出
              </button>
            </div>
          ) : null}
        </div>
      </header>

      {showChangelog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setShowChangelog(false)}
        >
          <div
            className={`${isDark ? "bg-gray-900" : "bg-white"} rounded-2xl shadow-xl w-full max-w-md mx-4 max-h-[80vh] flex flex-col`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className={`flex items-center justify-between px-5 py-4 border-b ${isDark ? "border-gray-700" : "border-gray-100"}`}>
              <div>
                <h2 className={`text-base font-semibold ${isDark ? "text-gray-100" : "text-gray-900"}`}>更新日志</h2>
                <p className="text-xs text-gray-400 mt-0.5">当前版本 {APP_VERSION}</p>
              </div>
              <button
                onClick={() => setShowChangelog(false)}
                className={`p-1.5 rounded-lg text-gray-400 ${isDark ? "hover:text-gray-300 hover:bg-gray-800" : "hover:text-gray-600 hover:bg-gray-100"} transition-colors`}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="overflow-y-auto px-5 py-4 space-y-5">
              {CHANGELOG.map((release) => (
                <div key={release.version}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`text-sm font-semibold ${isDark ? "text-gray-100" : "text-gray-900"} font-mono`}>{release.version}</span>
                    <span className="text-xs text-gray-400">{release.date}</span>
                    {release.version === APP_VERSION && (
                      <span className={`text-xs px-1.5 py-0.5 rounded ${isDark ? "bg-amber-500/10 text-amber-400" : "bg-blue-50 text-blue-600"}`}>当前</span>
                    )}
                  </div>
                  <ul className="space-y-1">
                    {release.items.map((item, i) => (
                      <li key={i} className={`flex items-start gap-2 text-sm ${isDark ? "text-gray-400" : "text-gray-600"}`}>
                        <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-gray-300 shrink-0" />
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {showMcpGuide && <McpGuideModal onClose={() => setShowMcpGuide(false)} />}
    </>
  );
}
