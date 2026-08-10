"""子体裁细分分析器 —— 对大分类自动拆分子体裁并逐一研究。

用户要求："每个大的体裁要进一步细分"
          "尽量分出最多的写作种类"

对文章数超过阈值的大分类（如新闻动态2683篇），自动：
1. 用关键词将文章分入不同子体裁
2. 对每个子体裁独立分析写作方法论
3. 汇总为该分类的子体裁风格指南
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ..llm.multi_llm import MultiLLMClient
from ..llm.json_utils import extract_json
from ..utils.logger import get_logger
from ..utils.text import truncate

_log = get_logger("processor.subgenre")

# 全部栏目 → 子体裁关键词映射（每个栏目都必须细分）
SUBGENRE_RULES: dict[str, dict[str, list[str]]] = {
    # ── 会议纪要 ──────────────────────────────
    "hyjy_zcbsyb": {
        "项目专题会纪要": ["专题", "技术", "方案", "变更", "设计", "锚索", "注浆", "TBM"],
        "项目推进会纪要": ["推进", "调度", "进度", "协调", "督"],
        "项目经理会纪要": ["项目经理", "预备会", "项目经理会"],
        "年度/务虚会纪要": ["务虚", "年度", "年终", "总结", "工作会"],
        "安全/质量专题会纪要": ["安全", "质量", "隐患", "应急", "事故"],
        "启动/评审会纪要": ["启动", "评审", "审查", "验收"],
    },
    # ── 领导讲话 ──────────────────────────────
    "ldjh_zcb": {
        "年度工作会议报告": ["工作会议", "年度", "工作报告", "总结讲话"],
        "安全生产工作报告": ["安全生产", "安委会", "安全工作"],
        "从严治党工作报告": ["治党", "廉政", "纪检", "党廉"],
        "党代会工作报告": ["党代会", "党员代表", "党委"],
    },
    # ── 规章制度 ──────────────────────────────
    "gzzd_zcb": {
        "考核管理办法": ["考核", "评价", "绩效"],
        "采购管理办法": ["采购", "招标", "投标", "供应商"],
        "人事管理办法": ["人员", "考勤", "聘用", "外聘", "职务"],
        "项目管理办法": ["项目", "验收", "沟通", "质量事故", "分包"],
        "财务管理办法": ["预算", "经费", "通讯费", "差旅", "报销"],
        "廉洁从业规定": ["廉洁", "从业", "反腐"],
        "印章文档管理办法": ["印章", "文档", "档案", "保密"],
        "评优管理办法": ["评优", "评选", "表彰"],
    },
    # ── 企务公开 ──────────────────────────────
    "qwgk_zcb": {
        "干部任前公示": ["任前公示", "公示"],
        "干部考察预告": ["考察预告", "考察"],
        "项目经理公示": ["项目经理"],
        "评优结果公示": ["评优", "表彰", "先进", "优秀"],
        "考核结果公示": ["考核", "结果"],
    },
    # ── 新闻动态（最大分类，细分最细） ────────────
    "xwdt_zcb": {
        "领导视察检查报道": ["视察", "检查", "调研", "指导", "慰问", "督导", "莅临",
                          "一行到", "深入", "听取汇报", "实地"],
        "工程节点进展报道": ["截流", "封顶", "贯通", "完工", "竣工", "开工", "蓄水",
                          "发电", "浇筑", "吊装", "拆除", "拆卸", "完成", "顺利",
                          "里程碑", "节点", "进度", "钻进", "掘进", "衬砌"],
        "会议座谈报道": ["召开", "会议", "座谈", "研讨", "交流", "推进会",
                     "工作会", "部署", "动员", "预备会"],
        "安全质量报道": ["安全", "质量", "隐患", "排查", "应急", "演练", "事故",
                     "专项检查", "大检查", "监督", "稽察", "整改"],
        "党建廉政报道": ["党建", "党日", "支部", "党员", "红色", "清廉", "纪检",
                     "廉政", "学习", "十九大", "二十大", "习近平", "主题教育",
                     "战斗堡垒", "先锋", "党课", "党章"],
        "培训学习报道": ["培训", "讲座", "宣贯", "教育", "课程",
                     "视频", "考试", "比武", "竞赛"],
        "人物事迹报道": ["人物", "事迹", "风采", "侧记", "扎根", "担当",
                     "出彩", "模范", "先进事迹", "最美", "副总", "工程师"],
        "节日文化活动报道": ["节", "活动", "联欢", "运动会", "文艺", "庆",
                      "三八", "国庆", "中秋", "春节", "元旦", "七·一",
                      "建党", "插花", "健步走", "观景", "摄影", "书画"],
        "表扬感谢信报道": ["表扬信", "感谢信", "贺信", "发来", "锦旗"],
        "获奖荣誉报道": ["获奖", "荣誉", "表彰", "称号", "先进", "优秀",
                     "文明工地", "鲁班奖", "大奖", "荣获"],
        "签约合同报道": ["签约", "合同", "中标", "授标"],
    },
    # ── 重要通知 ──────────────────────────────
    "zytz_zcb": {
        "人事任免通知": ["任免", "聘任", "解聘", "职务", "班子", "任职"],
        "会议通知": ["召开", "会议通知", "参加", "大会"],
        "制度发布通知": ["发布", "办法", "规定", "制度", "执行"],
        "安全管理通知": ["安全", "隐患", "应急", "防控", "防汛", "消防"],
        "考核评优通知": ["考核", "评优", "评选", "表彰"],
        "放假值班通知": ["放假", "春节", "国庆", "假期", "值班"],
        "选拔竞聘通知": ["选拔", "竞聘", "面试", "公开选拔"],
        "报送材料通知": ["报送", "材料", "总结", "报告", "截止"],
    },
    # ── 委河韵动（员工文化） ──────────────────
    "whyd_zcb": {
        "员工心得感悟": ["心得", "感想", "感悟", "体会", "发言"],
        "文学文艺作品": ["楹联", "诗词", "散文", "作品集", "征文"],
        "培训心得": ["培训", "学习", "GET", "经验", "观点"],
        "活动纪实": ["活动", "纪实", "侧记", "征文"],
        "奋斗故事": ["奋斗", "坚守", "青春", "追梦", "砥砺"],
    },
    # ── 安全生产 ──────────────────────────────
    "aqsc_zcb": {
        "应急演练报道": ["演练", "应急", "救援"],
        "安全检查通报": ["检查", "督查", "稽察", "监督", "排查"],
        "安全培训报道": ["培训", "宣贯", "学习", "讲座"],
        "安全管理通知": ["通知", "管理", "做好", "加强", "开展"],
        "安全知识试题": ["试题", "测试", "答案", "解析"],
    },
    # ── 党风廉政 ──────────────────────────────
    "dqwh_zcb": {
        "支部建设/任命": ["支部", "成立", "任命", "委员会"],
        "主题党日活动": ["党日", "主题", "活动", "入党", "誓词"],
        "优秀党员事迹": ["优秀", "党员", "事迹", "风采", "先进"],
        "廉政教育": ["廉政", "廉洁", "纪律", "教育", "党课"],
        "党建学习": ["学习", "十九大", "二十大", "习近平", "观看"],
    },
    # ── 国企文化 ──────────────────────────────
    "gwhb_zcb": {
        "人事聘任通知": ["聘任", "任免", "职务", "聘用", "解聘"],
        "制度办法发布": ["办法", "规定", "制度", "管理", "考核"],
        "会议活动通知": ["通知", "会议", "召开", "活动"],
        "工作动态报道": ["工作", "开展", "组织", "实施"],
        "评优表彰公示": ["表彰", "优秀", "先进", "评优", "公示"],
    },
    # ── 领导周工作安排 ────────────────────────
    "gbmxmzyfzrdt_zcb": {
        "周工作安排表（早期）": [],  # 按时间段自动分
    },
    # ── 项目周报 ──────────────────────────────
    "xmpbxxb_zcb": {
        "督导结果通报": ["督导", "通报", "检查"],
        "督办落实清单": ["督办", "落实", "清单", "议定事项"],
        "质量自查报告": ["质量", "自查", "报告"],
        "设计管理自查": ["设计管理", "自查"],
    },
    # ── 项目生产动态 ──────────────────────────
    "xmscdtjdb_zcb": {
        "月度生产情况报告": ["生产情况", "主要问题", "月"],
    },
    # ── 项目设计管理 ──────────────────────────
    "xmsjgldtb_zcb": {
        "设计管理监控台账": ["监控", "台账"],
    },
    # ── 月考勤统计 ────────────────────────────
    "sybykqtjb_zcb": {
        "月度考勤统计表": ["考勤", "统计"],
    },
    # ── 组织机构 ──────────────────────────────
    "zzjg_zcb": {
        "组织机构图": ["机构图", "组织机构"],
        "领导分工": ["分工", "领导"],
        "人员名单": ["人员名单", "名单"],
    },
}

# 子体裁分析 prompt
_SUBGENRE_ANALYZE_PROMPT = """你是企业公文写作分析专家。以下是从"{parent_genre}"分类中提取的"{sub_genre}"子类型文章样本。

请深入分析这一子体裁的写作方法论。

文章样本（{n}篇）：
{samples}

请输出以下JSON：
{{
  "子体裁名称": "{sub_genre}",
  "归属大类": "{parent_genre}",
  "子体裁定位": "（这一子体裁在企业宣传中的特定功能）",
  "标题规律": "（标题的命名模式和规范）",
  "结构范式": "（文章如何组织——开头/主体/结尾的标准结构）",
  "语言特色": ["（该子体裁特有的用词、句式、表达方式）"],
  "数据使用": "（如何引用数据、指标、标准编号等）",
  "与同类差异": "（与其他新闻子体裁相比的独特之处）",
  "写作要点": "（300字以内的写作方法论，供AI直接模仿）"
}}"""


def analyze_subgenres(
    llm: MultiLLMClient,
    crawl_dir: Path,
    parent_category: str,
    parent_genre_name: str,
    max_per_subgenre: int = 10,
    max_chars_per_article: int = 2500,
) -> list[dict]:
    """对一个大的体裁分类自动拆分子体裁并逐一分析。

    Args:
        llm: LLM 客户端
        crawl_dir: 爬取内容根目录
        parent_category: 分类目录名（如 xwdt_zcb）
        parent_genre_name: 中文名（如 "新闻动态"）
        max_per_subgenre: 每个子体裁最多分析文章数
        max_chars_per_article: 每篇文章最大字符数
    Returns:
        各子体裁的分析结果列表
    """
    rules = SUBGENRE_RULES.get(parent_category)
    if not rules:
        _log.warning("分类 %s 无子体裁定义，跳过", parent_category)
        return []

    cat_dir = crawl_dir / parent_category
    if not cat_dir.exists():
        return []

    # Step 1: 读取所有文章并按子体裁分类
    articles_by_subgenre = _classify_articles(cat_dir, rules)

    if not articles_by_subgenre:
        _log.warning("分类 %s 分类后无文章", parent_category)
        return []

    _log.info("分类 %s 子体裁分布:", parent_genre_name)
    for sub_name, arts in sorted(articles_by_subgenre.items(), key=lambda x: -len(x[1])):
        _log.info("  %s: %d篇", sub_name, len(arts))

    # Step 2: 逐个子体裁分析
    results: list[dict] = []
    for sub_name, articles in articles_by_subgenre.items():
        if len(articles) < 2:
            _log.info("子体裁 %s 仅 %d 篇，跳过", sub_name, len(articles))
            continue

        samples = _build_samples(articles, max_per_subgenre, max_chars_per_article)
        _log.info("分析子体裁: %s/%s（%d篇，%d字）",
                  parent_genre_name, sub_name, samples["count"], samples["total_chars"])

        try:
            analysis = _analyze_one_subgenre(
                llm, parent_genre_name, sub_name, samples
            )
            results.append(analysis)
        except Exception as exc:
            _log.warning("子体裁 %s 分析失败: %s", sub_name, str(exc)[:80])

    return results


def _classify_articles(
    cat_dir: Path,
    rules: dict[str, list[str]],
) -> dict[str, list[tuple[str, str]]]:
    """按关键词将文章分类到子体裁。

    Returns:
        {子体裁名: [(文件名, 正文), ...]}
    """
    result: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for f in cat_dir.glob("*.txt"):
        if f.name == "index.txt":
            continue
        try:
            text = f.read_text(encoding="utf-8")
            parts = text.split("=" * 60, 1)
            body = parts[1].strip() if len(parts) > 1 else ""
            if len(body) < 100:
                continue

            # 提取标题（正文第一行）
            title_line = body.split("\n")[0].strip() if body else ""

            # 按优先级匹配子体裁
            matched = False
            for sub_name, keywords in rules.items():
                # 检查标题和前500字
                check_text = (title_line + " " + body[:500]).lower()
                for kw in keywords:
                    if kw in check_text or kw.lower() in check_text:
                        result[sub_name].append((f.name, body))
                        matched = True
                        break
                if matched:
                    break

            if not matched:
                result["其他新闻"].append((f.name, body))

        except Exception:
            continue

    # 过滤掉文章太少的子体裁（合并到"其他"）
    final: dict[str, list[tuple[str, str]]] = {}
    others: list[tuple[str, str]] = []
    for sub, arts in result.items():
        if sub == "其他新闻":
            others.extend(arts)
        elif len(arts) >= 3:
            final[sub] = arts
        else:
            others.extend(arts)

    if others:
        final["其他新闻"] = others

    return final


def _build_samples(
    articles: list[tuple[str, str]],
    max_count: int,
    max_chars: int,
) -> dict:
    """构建样本文本。"""
    # 按内容长度降序排列
    sorted_arts = sorted(articles, key=lambda x: -len(x[1]))
    selected = sorted_arts[:max_count]

    samples = []
    total = 0
    for _, body in selected:
        truncated = truncate(body, max_chars)
        samples.append(truncated)
        total += len(truncated)

    return {
        "samples": "\n---\n".join(samples),
        "count": len(selected),
        "total_chars": total,
    }


def _analyze_one_subgenre(
    llm: MultiLLMClient,
    parent_genre: str,
    sub_genre: str,
    samples: dict,
) -> dict:
    """分析单个子体裁。"""
    raw = llm.chat(
        system_prompt="你是企业公文写作分析专家。只输出JSON，不要markdown围栏。",
        user_prompt=_SUBGENRE_ANALYZE_PROMPT.format(
            parent_genre=parent_genre,
            sub_genre=sub_genre,
            n=samples["count"],
            samples=truncate(samples["samples"], max_chars=30000),
        ),
        json_mode=True,
        temperature=0.3,
        prefer_paid=True,
        max_tokens=4096,
    )

    if not raw or not raw.strip():
        return {"子体裁名称": sub_genre, "分析": "失败"}

    return extract_json(raw)


def analyze_all_subgenres(
    llm: MultiLLMClient,
    crawl_dir: Path,
    max_per_subgenre: int = 10,
    max_chars_per_article: int = 2500,
) -> dict[str, list[dict]]:
    """对【所有】栏目进行子体裁细分分析。

    用户要求："所有栏目都要进行子体裁细分研究"
              "不要把栏目当作一个整体体裁处理分析"

    Returns:
        {分类名: [子体裁分析结果, ...]}
    """
    parent_names = {
        "hyjy_zcbsyb": "会议纪要",
        "ldjh_zcb": "领导讲话",
        "gzzd_zcb": "规章制度",
        "qwgk_zcb": "企务公开",
        "xwdt_zcb": "新闻动态",
        "zytz_zcb": "重要通知",
        "whyd_zcb": "委河韵动",
        "aqsc_zcb": "安全生产",
        "dqwh_zcb": "党风廉政",
        "gwhb_zcb": "国企文化",
        "gbmxmzyfzrdt_zcb": "领导周工作安排",
        "xmpbxxb_zcb": "项目周报",
        "xmscdtjdb_zcb": "项目生产动态",
        "xmsjgldtb_zcb": "项目设计管理",
        "sybykqtjb_zcb": "月考勤统计",
        "zzjg_zcb": "组织机构",
    }

    all_results: dict[str, list[dict]] = {}

    for cat_code, cat_name in parent_names.items():
        cat_dir = crawl_dir / cat_code
        if not cat_dir.exists():
            continue

        txt_count = len([f for f in cat_dir.glob("*.txt") if f.name != "index.txt"])
        if txt_count == 0:
            continue

        rules = SUBGENRE_RULES.get(cat_code)

        # 没有子体裁规则的栏目 → 整体作为一个子体裁分析
        if not rules:
            _log.info("=" * 50)
            _log.info("栏目 %s 整体分析（%d篇，格式统一）", cat_name, txt_count)
            _log.info("=" * 50)

            articles = _collect_all_articles(cat_dir)
            if articles:
                samples = _build_samples(articles, max_per_subgenre, max_chars_per_article)
                _log.info("分析: %s（%d篇，%d字）", cat_name, samples["count"], samples["total_chars"])
                try:
                    analysis = _analyze_one_subgenre(llm, cat_name, cat_name + "（整体格式）", samples)
                    all_results[cat_name] = [analysis]
                except Exception as exc:
                    _log.warning("栏目 %s 分析失败: %s", cat_name, str(exc)[:80])
            continue

        # 有子体裁规则的栏目 → 细分分析
        _log.info("=" * 50)
        _log.info("栏目 %s 子体裁细分分析（%d篇）", cat_name, txt_count)
        _log.info("=" * 50)

        # 检查规则是否只有空关键词的特殊栏目（如周工作安排）
        has_real_rules = any(kws for kws in rules.values())
        if not has_real_rules:
            # 格式统一的栏目，按时间段拆分
            articles = _collect_all_articles(cat_dir)
            if articles:
                # 按年份分组
                by_year: dict[str, list] = defaultdict(list)
                for fname, body in articles:
                    year_match = re.search(r"(20\d{2})", fname)
                    year = year_match.group(1) if year_match else "其他"
                    by_year[year].append((fname, body))

                for year, year_arts in sorted(by_year.items()):
                    if len(year_arts) < 2:
                        continue
                    samples = _build_samples(year_arts, max_per_subgenre, max_chars_per_article)
                    sub_name = f"{cat_name}（{year}年）"
                    _log.info("分析: %s（%d篇，%d字）", sub_name, samples["count"], samples["total_chars"])
                    try:
                        analysis = _analyze_one_subgenre(llm, cat_name, sub_name, samples)
                        all_results.setdefault(cat_name, []).append(analysis)
                    except Exception as exc:
                        _log.warning("子体裁 %s 分析失败: %s", sub_name, str(exc)[:80])
            continue

        sub_results = analyze_subgenres(
            llm, crawl_dir, cat_code, cat_name,
            max_per_subgenre, max_chars_per_article,
        )

        if sub_results:
            all_results[cat_name] = sub_results
            _log.info("栏目 %s: 识别出 %d 个子体裁", cat_name, len(sub_results))

    return all_results


def _collect_all_articles(cat_dir: Path) -> list[tuple[str, str]]:
    """读取目录下所有文章。"""
    articles = []
    for f in cat_dir.glob("*.txt"):
        if f.name == "index.txt":
            continue
        try:
            text = f.read_text(encoding="utf-8")
            parts = text.split("=" * 60, 1)
            body = parts[1].strip() if len(parts) > 1 else ""
            if len(body) >= 100:
                articles.append((f.name, body))
        except Exception:
            continue
    return articles
