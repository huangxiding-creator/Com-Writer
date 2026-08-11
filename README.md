# 企业写手 Com-Writer

> **原始材料进去，成稿文件出来** —— 全程自动、质量可控、格式规范的企业写作自动化工具。

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 概述

Com-Writer 是一款通用的企业内部写作自动化工具。它能够：

1. **爬取** —— 整站全量爬取任意内网/外网网站（通用 BFS 爬虫，自动检测站点结构）
2. **提炼** —— 从爬取的历史文档中系统性提炼企业写作方法论（用词、句式、结构、语气）
3. **理解** —— AI 深度理解会议录音转写稿，自动识别议题、决策、行动项、关键数据
4. **生成** —— 按企业风格和方法论，自动生成符合模板格式的正式公文
5. **质控** —— AI 自审数据一致性与完整性，不达标自动返工（最多 3 轮）
6. **输出** —— 基于用户 Word 模板生成最终文档，完整保留原格式样式

### 核心管线

```
原始材料（录音转写 / 会议记录 / 技术资料）
        ↓
  [整站爬虫] → 全量爬取企业网站 → 分类存储
        ↓
  [风格提炼] → 系统性提炼写作方法论 → 注入生成器
        ↓
  [AI 理解] → 结构化提取（议题 / 决策 / 行动项 / 关键数据）
        ↓
  [正式撰写] → 按企业风格 + 模板格式生成公文
        ↓
  [质量自审] → 数据一致性 / 完整性 / 规范性检查 → 自动返工
        ↓
  [模板输出] → 基于用户 .docx 模板生成最终 Word 文档
```

## 快速开始

### 环境要求

- Python 3.10+
- Windows / macOS / Linux
- 网络可访问智谱 AI 或 DeepSeek API

### 安装

```bash
git clone https://github.com/huangxiding-creator/Com-Writer.git
cd Com-Writer
pip install -r requirements.txt
```

### 配置

1. 复制环境变量模板并填入密钥：

```bash
cp .env.example .env
```

编辑 `.env`：

```ini
# 工作目录名（按企业/部门组织）
COM_WRITER_WORKSPACE=workspace

ZHIPU_API_KEY=your_zhipu_api_key
DEEPSEEK_API_KEY=your_deepseek_api_key
INTRANET_TOKEN=your_intranet_token
WECOM_WEBHOOK_URL=your_webhook_url
```

2. 编辑 `config.ini` 配置爬取地址、模型参数、模板路径等（所有参数均可调）。

### 使用

```bash
# 自动查找最新记录生成会议纪要
python run.py

# 指定输入文件
python run.py -i "path/to/transcript.docx"

# 先爬取内网→提炼写作方法论→再生成会议纪要
python run.py --crawl

# 质量优先模式（使用 GLM-5.2 付费模型）
python run.py --quality

# 指定输出路径和文号
python run.py -o "output.docx" -n "〔2026〕2号"

# 调试模式
python run.py --debug

# 查看可用插件
python run.py --list-plugins
```

## 架构设计

### 目录结构

```
Com-Writer/
├── run.py                          # CLI 入口
├── config.ini                      # 用户配置（所有可调参数）
├── .env                            # 密钥文件（不提交到 Git）
├── requirements.txt                # Python 依赖
│
├── src/
│   ├── config/                     # 配置加载 & 路径管理
│   │   ├── loader.py               #   INI 配置 + ${VAR} 展开 + 热重载
│   │   └── paths.py                #   项目路径常量
│   │
│   ├── llm/                        # 多模型 LLM 客户端
│   │   ├── zhipu.py                #   智谱 GLM（免费链 + 自适应限速）
│   │   ├── deepseek.py             #   DeepSeek（tenacity 重试）
│   │   ├── multi_llm.py            #   双引擎统一客户端（自动降级）
│   │   └── json_utils.py           #   稳健 JSON 提取
│   │
│   ├── browser/                    # 浏览器自动化
│   │   ├── intranet_crawler.py     #   通用整站 BFS 爬虫
│   │   └── driver.py               #   DrissionPage 浏览器驱动
│   │
│   ├── processors/                 # AI 处理管线
│   │   ├── understander.py         #   Step 1: 深度理解（转写稿→结构化）
│   │   ├── generator.py            #   Step 2: 正式撰写（结构化→公文）
│   │   ├── quality_gate.py         #   Step 3: 质量自审（自检+返工）
│   │   └── style_extractor.py      #   写作方法论提炼器
│   │
│   ├── writers/                    # 文档输出
│   │   ├── docx_writer.py          #   基于模板的 Word 生成
│   │   └── template_engine.py      #   模板结构分析
│   │
│   ├── readers/                    # 文件读取
│   │   ├── docx_reader.py          #   .docx 文件读取
│   │   └── transcript_parser.py    #   ASR 转写稿解析
│   │
│   ├── notify/                     # 通知
│   │   └── wecom.py                #   企业微信 Webhook 通知
│   │
│   ├── core/                       # 核心框架
│   │   ├── orchestrator.py         #   管线编排器
│   │   ├── models.py               #   不可变数据模型
│   │   ├── checkpoint.py           #   检查点恢复
│   │   └── plugin_base.py          #   插件基类
│   │
│   └── utils/                      # 工具
│       ├── logger.py               #   日志（UTF-8 + 文件双输出）
│       └── text.py                 #   文本处理
│
├── plugins/                        # 写作体裁插件
│   ├── meeting_minutes.py          #   会议纪要（生产级）
│   ├── work_report.py              #   工作报告（预留）
│   ├── technical_proposal.py       #   技术方案（预留）
│   └── registry.py                 #   插件注册表
│
└── workspace/                      # 工作目录（通过 .env 的 COM_WRITER_WORKSPACE 配置）
    ├── 00 内网文字材料爬取/         #   爬取的网页内容
    ├── 01 内部写作成果提炼/         #   提炼的写作方法论
    ├── 02 内部写作体裁模板/         #   Word 模板文件
    ├── 03 原始记录资料/             #   输入的转写稿/记录
    └── 04 自动写作成果/             #   生成的正式文档
```

### 技术亮点

#### 1. 通用整站爬虫（适应任意网站）

- **自动检测站点前缀**：从起始 URL 自动提取路径根，不硬编码任何站点路径
- **BFS 全量遍历**：递归发现并访问所有同域页面，URL 自动去重
- **内容质量过滤**：区分有效内容页（>200字）和导航页，分类存储
- **文档下载**：自动下载 .docx/.pdf/.xls 等附件
- **礼貌延迟**：请求间隔避免压垮服务器
- **编码自动检测**：支持 UTF-8/GBK 等多种编码

#### 2. 系统性写作方法论提炼

- **按内容质量排序**：优先分析内容丰富的文章，跳过导航页
- **多维度分析**：思维方式、话语体系、结构范式、表达风格、格式规范
- **方法论总结**：不仅罗列词汇，更提炼深层写作规律（500字方法论）
- **Prompt 注入**：格式化为结构化参考文本，直接注入生成器

#### 3. 多模型 AI 引擎（双引擎自动降级）

```
默认链：GLM 免费模型链（flashx → flash → airx → air）
       ↘ 全部限流 → DeepSeek 自动接管

质量优先：GLM-5.2 付费模型 → 免费链 → DeepSeek
```

- **自适应限速**：_AdaptivePacer（成功加速 0.7×，限流退避 2.0×）
- **跨引擎降级**：GLM 全线失败时自动切换 DeepSeek
- **稳健 JSON**：extract_json 支持 markdown 围栏去除、正则提取

#### 4. 4 步 AI 处理管线

| 步骤 | 模块 | 输入 → 输出 | 特点 |
|------|------|-------------|------|
| 理解 | understander | 转写稿 → MeetingAnalysis | 修正 ASR 方言误识别，保留技术参数原值 |
| 生成 | generator | MeetingAnalysis → GeneratedContent | 注入写作方法论，聚焦最终共识 |
| 质控 | quality_gate | 生成文本 → QualityReport | 本地快检 + LLM 审查，自动返工 |
| 输出 | docx_writer | GeneratedContent → .docx | 模板格式完整保留 |

#### 5. 模板格式保留

- 复制用户 .docx 模板作为基础
- 仅替换内容段落，保留所有样式（字体/字号/间距/编号）
- 模板引擎自动分析字段位置和正文区域
- 字段值智能更新（会议类别/时间/地点/主持人/参会人员）

#### 6. 插件架构

```python
from src.core.plugin_base import WritingPlugin, PluginMeta

class MyPlugin(WritingPlugin):
    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(name="我的体裁", code="my_type", description="...")

    def run(self, input_path, **kwargs):
        # 实现写作逻辑
        return TaskResult(success=True, output_path="...")
```

注册后即可通过 `python run.py -p my_type` 调用。

## 配置说明

### config.ini 主要配置项

| 配置节 | 关键参数 | 说明 |
|--------|----------|------|
| `[智谱]` | `免费模型` | GLM 免费模型链（逗号分隔） |
| `[智谱]` | `付费模型` | 质量优先时使用的模型 |
| `[内网爬取]` | `地址` | 任意网站 URL（自动检测站点结构） |
| `[内网爬取]` | `token` | URL 认证令牌（可选） |
| `[会议纪要]` | `质量自审轮次` | AI 自审返工次数（默认 3） |
| `[会议纪要]` | `质量阈值` | 通过分数线（默认 80） |
| `[通知]` | `企微webhook` | 企业微信 Webhook URL |

### AI 模型选择策略

| 场景 | 推荐模型 | 原因 |
|------|----------|------|
| 理解阶段（关键） | GLM-5.2 → 免费链 | 准确性最重要，免费兜底 |
| 生成阶段 | 免费/付费均可 | 创造性任务，免费模型胜任 |
| 质量审查 | 付费优先 | 审查需要更强的推理 |
| 风格提炼 | 付费优先 | 分析需要更深的理解 |

## 实际效果

以某水利枢纽工程专题会议（90分钟录音，26000字转写稿）为例：

| 维度 | 手工方式 | Com-Writer |
|------|----------|------------|
| 输入 | 人工听读 90 分钟 | 自动读取转写稿 |
| 爬取+提炼 | 无 | 57 页整站爬取 → 16 篇精选 → 25000 字分析 |
| 理解 | 人工提炼 | 2 议题 / 7 行动项 / 8 关键数据点 |
| 生成 | 30-60 分钟手写 | 3 段正式公文（含风格方法论） |
| 质控 | 因人而异 | AI 自审 95/100 |
| 耗时 | 2-3 小时 | ~110 秒 |

## 借鉴的开源项目

本项目借鉴了以下项目的成熟设计：

- **We-AIPO**：多 LLM 客户端、自适应限速器、配置加载器、浏览器驱动、企业微信通知
- **DrissionPage**：反检测浏览器自动化
- **python-docx**：Word 文档模板操作

## 开发计划

- [x] Phase 1: 会议纪要生成管线（生产级）
- [x] Phase 2: 整站爬虫 + 写作方法论提炼
- [x] Phase 3: 插件架构（会议纪要/工作报告/技术方案）
- [ ] GUI 模式（CLI 生产稳定后开发）
- [ ] 更多写作体裁插件（按需扩展）

## License

MIT License - 详见 [LICENSE](LICENSE)
