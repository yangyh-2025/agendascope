"""统计佐证计算（T3.10，详细设计 4.2 算法 4 evidence 部分 + 2.129 数据不足 4004）。

对议题 T 的 origin_country → follower_countries 计算三类统计证据：
  1) 时滞互相关 XCorr（lag 0..stats_xcorr_max_lag_days，Pearson 互相关 + t 检验）
  2) Granger 因果（statsmodels.tsa.stattools.grangercausalitytests，方向 origin→follower）
  3) QAP（置换检验评估"国家-日期报道计数矩阵"相关性；简化用 pearsonr + 行置乱，
     完整 MRQAP 多自变量扩展留 M3-3）

样本量硬性规则（详细设计 2.129 / 4.2 算法 4 注释）：
  议题内 window_days 窗口总文章数 < stats_min_articles (默认 100) → 全部检验返回 None，
  insufficient_data=True，rejection_reason 含"数据量不足"，绝不输出误导性结论。

降级原则（详细设计 2.129 表 agenda_engine 行）：
  统计是证据不是正确性依赖——任何单项检验失败（常数序列/数值不稳定/异常）均返回 None
  并把原因写进 rejection_reason，不抛异常；事件判定层用其余证据并降置信度。
"""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import numpy as np
from scipy import stats as scipy_stats
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agenda_engine.config import get_agenda_settings
from app.core.logging import get_logger
from app.models.article import Article
from app.models.topic import TopicArticle

logger = get_logger("agenda_engine.stats_evidence")


@dataclass(frozen=True)
class XCorrResult:
    """时滞互相关结果：max_correlation 取 |ρ| 最大，best_lag_days 为对应滞后（天）。"""

    max_correlation: float
    best_lag_days: int
    p_value: float
    significant: bool


@dataclass(frozen=True)
class GrangerResult:
    """Granger 因果结果：方向固定 origin → follower，F 统计量与最小 p 值对应 lag。"""

    f_statistic: float
    p_value: float
    best_lag_days: int
    significant: bool


@dataclass(frozen=True)
class QAPResult:
    """QAP 置换检验结果：网络相关系数 + 置换分布 p 值。"""

    correlation: float
    p_value: float
    significant: bool
    permutations: int


@dataclass(frozen=True)
class StatsEvidence:
    """单议题统计佐证聚合：硬阈值不达标时 xcorr/granger/qap 全 None。"""

    article_count: int
    xcorr: XCorrResult | None
    granger: GrangerResult | None
    qap: QAPResult | None
    insufficient_data: bool
    rejection_reason: str | None


# ---------------------------------------------------------------------------
# 数据准备：从 articles ⋈ topic_articles 拉取窗口内每日每国报道计数
# ---------------------------------------------------------------------------


def _day_floor_utc(ts: datetime) -> datetime:
    """UTC 日粒度 00:00 地板（naive 输入按 UTC 处理）。"""
    ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    return datetime(ts.year, ts.month, ts.day, tzinfo=UTC)


def _fetch_daily_counts(
    db: Session,
    topic_id: UUID,
    *,
    window_days: int,
    now: datetime | None = None,
) -> tuple[int, dict[str, dict[datetime, int]], int]:
    """取议题近 window_days 内按"国家 × 日"的报道计数。

    返回 (article_count, counts_by_country, window_days_effective)：
      - article_count：窗口内该议题归属文章总数（含全部国家，作为样本量硬阈值分母）
      - counts_by_country：{country_code: {day_floor_utc: count}}
      - window_days_effective：实际使用的窗口天数（与调用方传入一致）
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)

    # 议题内总文章数（不区分国家，含 follower 之外的国家；与"样本量 <100 拒绝"分母口径对齐）
    total = int(
        db.scalar(
            select(func.count())
            .select_from(TopicArticle)
            .join(Article, Article.id == TopicArticle.article_id)
            .where(
                TopicArticle.topic_id == topic_id,
                Article.published_at >= cutoff,
            )
        )
        or 0
    )

    # 按国家×日聚合
    rows = db.execute(
        select(Article.country_code, Article.published_at)
        .join(TopicArticle, TopicArticle.article_id == Article.id)
        .where(
            TopicArticle.topic_id == topic_id,
            Article.published_at >= cutoff,
        )
    ).all()

    counts: dict[str, dict[datetime, int]] = {}
    for country, published_at in rows:
        day = _day_floor_utc(published_at)
        counts.setdefault(country, {})[day] = counts.setdefault(country, {}).get(day, 0) + 1
    return total, counts, window_days


def _series_for_country(
    country_counts: dict[datetime, int],
    *,
    window_days: int,
    now: datetime | None = None,
) -> np.ndarray:
    """把 {day: count} 展开为长度 window_days 的等间距日序列（缺失日补 0）。"""
    now = now or datetime.now(UTC)
    end_day = _day_floor_utc(now)
    start_day = end_day - timedelta(days=window_days - 1)
    series = np.zeros(window_days, dtype=float)
    for i in range(window_days):
        day = start_day + timedelta(days=i)
        series[i] = float(country_counts.get(day, 0))
    return series


# ---------------------------------------------------------------------------
# XCorr：lag 0..max_lag 的 Pearson 互相关 + t 检验
# ---------------------------------------------------------------------------


def _xcorr_pair(
    origin: np.ndarray,
    follower: np.ndarray,
    *,
    max_lag: int,
    alpha: float,
) -> XCorrResult | None:
    """单对 origin→follower 的时滞互相关（origin 领先 follower lag 天时 lag 为正）。"""
    if origin.size != follower.size or origin.size < 3:
        return None
    # 常数序列无方差 → 相关系数无意义，跳过
    if np.std(origin) == 0.0 or np.std(follower) == 0.0:
        return None

    n = origin.size
    best_corr = 0.0
    best_lag = 0
    for lag in range(0, max_lag + 1):
        # lag>0：origin[0..n-lag-1] 对 follower[lag..n-1]（origin 领先 lag 天）
        if lag == 0:
            x = origin
            y = follower
        else:
            x = origin[: n - lag]
            y = follower[lag:]
        if x.size < 3:
            continue
        # 任一截段子序列常数 → 该 lag 无意义，跳过
        if np.std(x) == 0.0 or np.std(y) == 0.0:
            continue
        corr, _p = scipy_stats.pearsonr(x, y)
        if not np.isfinite(corr):
            continue
        # 平局时优先较小 lag（更接近真实因果时滞，避免周期脉冲被长 lag 接住）
        if abs(corr) > abs(best_corr) + 1e-9:
            best_corr = float(corr)
            best_lag = lag

    if best_corr == 0.0 and best_lag == 0:
        # 全 lag 都没产生非零相关（理论上极少），用 lag=0 全序列做显著性
        try:
            corr, p = scipy_stats.pearsonr(origin, follower)
            if np.isfinite(corr) and np.isfinite(p):
                return XCorrResult(
                    max_correlation=float(corr),
                    best_lag_days=0,
                    p_value=float(p),
                    significant=bool(p < alpha),
                )
        except Exception:
            return None
        return None

    # t 检验 H0: ρ=0；t = r·sqrt((n-2)/(1-r^2))，自由度 n-2
    # n 取最佳 lag 处的有效对数
    n_eff = n - best_lag
    if n_eff < 3 or abs(best_corr) >= 1.0:
        # |r|=1 时 t→∞，p→0；与 pearsonr 退化行为对齐
        p_value = 0.0 if abs(best_corr) >= 1.0 else 1.0
    else:
        t_stat = abs(best_corr) * np.sqrt((n_eff - 2) / max(1e-12, 1.0 - best_corr * best_corr))
        p_value = float(2.0 * scipy_stats.t.sf(t_stat, df=n_eff - 2))
    return XCorrResult(
        max_correlation=best_corr,
        best_lag_days=int(best_lag),
        p_value=p_value,
        significant=bool(p_value < alpha),
    )


def _compute_xcorr(
    origin_series: np.ndarray,
    follower_series_list: list[np.ndarray],
    *,
    max_lag: int,
    alpha: float,
) -> XCorrResult | None:
    """多 follower 取平均最大相关（绝对值均值），保留最大绝对相关对应 lag。"""
    results: list[XCorrResult] = []
    for f in follower_series_list:
        r = _xcorr_pair(origin_series, f, max_lag=max_lag, alpha=alpha)
        if r is not None:
            results.append(r)
    if not results:
        return None
    avg_abs_corr = float(np.mean([abs(r.max_correlation) for r in results]))
    # 取绝对相关最大的那次作为代表 lag / p
    best = max(results, key=lambda r: abs(r.max_correlation))
    # 平均后无法直接做 t 检验（不同 lag 不同 n_eff），保守用代表检验的 p
    return XCorrResult(
        max_correlation=avg_abs_corr if best.max_correlation >= 0 else -avg_abs_corr,
        best_lag_days=best.best_lag_days,
        p_value=best.p_value,
        significant=bool(best.p_value < alpha),
    )


# ---------------------------------------------------------------------------
# Granger：statsmodels grangercausalitytests（H0: origin 不 Granger 引起 follower）
# ---------------------------------------------------------------------------


def _granger_pair(
    origin: np.ndarray,
    follower: np.ndarray,
    *,
    max_lag: int,
    alpha: float,
) -> GrangerResult | None:
    """单对 Granger 因果：方向 origin → follower。任一常数序列返回 None。"""
    if origin.size != follower.size or origin.size < max_lag + 3:
        return None
    if np.std(origin) == 0.0 or np.std(follower) == 0.0:
        return None
    try:
        from statsmodels.tsa.stattools import grangercausalitytests
    except ImportError:
        logger.warning("granger_statsmodels_missing")
        return None

    # statsmodels 接口约定：传入 2 列 ndarray，第 1 列是被解释变量 y，第 2 列是 x
    # 检验 H0：x 不 Granger 引起 y；要验 origin→follower，应把 follower 放第 1 列、origin 放第 2 列
    data = np.column_stack([follower, origin])
    best_p = 1.0
    best_f = 0.0
    best_lag = 0
    try:
        # 输出极啰嗦，捕 warnings 防污染日志
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            results = grangercausalitytests(data, maxlag=max_lag, verbose=False)
        for lag in range(1, max_lag + 1):
            test_result = results.get(lag)
            if not test_result:
                continue
            # test_result[0] 是 dict，含 'ssr_ftest' / 'params_ftest' 等
            ssr_f = test_result[0].get("ssr_ftest")
            if ssr_f is None:
                continue
            f_stat, p_value = float(ssr_f[0]), float(ssr_f[1])
            if not (np.isfinite(f_stat) and np.isfinite(p_value)):
                continue
            if p_value < best_p:
                best_p = p_value
                best_f = f_stat
                best_lag = lag
    except Exception as exc:  # 数值不稳定/InfeasibleError 等
        logger.info("granger_failed", reason=str(exc))
        return None

    if best_lag == 0:
        return None
    return GrangerResult(
        f_statistic=best_f,
        p_value=best_p,
        best_lag_days=int(best_lag),
        significant=bool(best_p < alpha),
    )


def _compute_granger(
    origin_series: np.ndarray,
    follower_series_list: list[np.ndarray],
    *,
    max_lag: int,
    alpha: float,
) -> GrangerResult | None:
    """多 follower 取最小 p 值对应 lag；任一 follower 显著即整体显著（方向已固定）。"""
    results: list[GrangerResult] = []
    for f in follower_series_list:
        r = _granger_pair(origin_series, f, max_lag=max_lag, alpha=alpha)
        if r is not None:
            results.append(r)
    if not results:
        return None
    best = min(results, key=lambda r: r.p_value)
    return best


# ---------------------------------------------------------------------------
# QAP：置换检验（行置乱保持每国总曝光量，检验国家-日期矩阵相关性）
# ---------------------------------------------------------------------------


def _qap_test(
    origin_series: np.ndarray,
    follower_series_list: list[np.ndarray],
    *,
    permutations: int,
    alpha: float,
    rng_seed: int = 42,
) -> QAPResult | None:
    """简化 QAP：把 origin 序列与 follower 均值序列做 lag 对齐后的最佳 Pearson 相关；
    随机置换 origin 的日期顺序 permutations 次得到零分布，
    p = (#|r_perm| >= |r_obs| + 1) / (permutations + 1)。

    完整 MRQAP（多自变量回归的矩阵置换检验）留 M3-3 扩展；当前实现对单议题/单 origin
    已能识别"日期对齐（允许整体平移）的相关是否显著强于随机"。常数序列返回 None。
    """
    if not follower_series_list:
        return None
    follower_mean = np.mean(np.stack(follower_series_list), axis=0)
    if np.std(origin_series) == 0.0 or np.std(follower_mean) == 0.0:
        return None

    def _best_corr(a: np.ndarray, b: np.ndarray) -> float:
        """在 lag 0..7 范围内取 |ρ| 最大的相关系数（与 xcorr 思路对齐）。"""
        n = a.size
        best = 0.0
        for lag in range(0, 8):
            if lag == 0:
                x, y = a, b
            else:
                x, y = a[: n - lag], b[lag:]
            if x.size < 3 or np.std(x) == 0.0 or np.std(y) == 0.0:
                continue
            try:
                c, _ = scipy_stats.pearsonr(x, y)
            except Exception:
                continue
            if np.isfinite(c) and abs(c) > abs(best) + 1e-9:
                best = float(c)
        return best

    r_obs = _best_corr(origin_series, follower_mean)
    if not np.isfinite(r_obs):
        return None

    rng = np.random.default_rng(rng_seed)
    exceed = 0
    for _ in range(permutations):
        perm = rng.permutation(origin_series)
        if np.std(perm) == 0.0:
            continue
        r_perm = _best_corr(perm, follower_mean)
        if np.isfinite(r_perm) and abs(r_perm) >= abs(r_obs) - 1e-12:
            exceed += 1
    p_value = (exceed + 1) / (permutations + 1)
    return QAPResult(
        correlation=float(r_obs),
        p_value=float(p_value),
        significant=bool(p_value < alpha),
        permutations=permutations,
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def compute_stats_evidence(
    db: Session,
    topic_id: UUID,
    origin_country: str,
    follower_countries: Iterable[str],
    *,
    window_days: int = 30,
    min_articles: int | None = None,
    now: datetime | None = None,
) -> StatsEvidence:
    """对议题 T 的 origin_country → follower_countries 计算统计佐证。

    数据准备：
      - 从 articles ⋈ topic_articles 取该议题窗口内的报道计数
      - 按"国家 × UTC 日粒度 00:00"展开为长度 window_days 的等间距序列（缺失日补 0）

    样本量硬性规则：
      - 窗口内议题总文章数 < stats_min_articles（默认 100）：所有检验返回 None，
        insufficient_data=True，rejection_reason=f"数据量不足（N<100）" ——绝不输出误导性结论

    失败降级：
      - 常数序列 / statsmodels 缺失 / 数值不稳定 → 对应检验返回 None，
        rejection_reason 累加说明；不抛异常（统计是证据不是正确性依赖）
    """
    settings = get_agenda_settings()
    threshold = min_articles if min_articles is not None else settings.stats_min_articles
    alpha = settings.stats_significance_alpha
    max_lag_x = settings.stats_xcorr_max_lag_days
    max_lag_g = settings.stats_granger_max_lag_days
    permutations = settings.stats_qap_permutations

    follower_list = [c for c in follower_countries if c and c != origin_country]
    now = now or datetime.now(UTC)

    article_count, counts_by_country, _ = _fetch_daily_counts(
        db, topic_id, window_days=window_days, now=now
    )

    # 硬性规则：样本量 <100 拒绝输出全部检验结论
    if article_count < threshold:
        reason = f"数据量不足（{article_count}<{threshold}）"
        logger.info(
            "stats_evidence_insufficient",
            topic_id=str(topic_id),
            article_count=article_count,
            threshold=threshold,
        )
        return StatsEvidence(
            article_count=article_count,
            xcorr=None,
            granger=None,
            qap=None,
            insufficient_data=True,
            rejection_reason=reason,
        )

    # 构造 origin/follower 序列
    origin_counts = counts_by_country.get(origin_country, {})
    origin_series = _series_for_country(origin_counts, window_days=window_days, now=now)
    follower_series_list: list[np.ndarray] = []
    for c in follower_list:
        series = _series_for_country(counts_by_country.get(c, {}), window_days=window_days, now=now)
        follower_series_list.append(series)

    rejection_parts: list[str] = []

    # XCorr
    xcorr_result: XCorrResult | None = None
    if not follower_series_list:
        rejection_parts.append("无跟随国序列")
    elif np.std(origin_series) == 0.0:
        rejection_parts.append("origin 序列无变化（常数）")
    else:
        try:
            xcorr_result = _compute_xcorr(
                origin_series, follower_series_list, max_lag=max_lag_x, alpha=alpha
            )
            if xcorr_result is None:
                rejection_parts.append("xcorr 计算返回空（常数 follower 或样本过短）")
        except Exception as exc:  # 兜底防御
            logger.info("xcorr_failed", reason=str(exc))
            rejection_parts.append(f"xcorr 异常：{exc}")

    # Granger
    granger_result: GrangerResult | None = None
    if follower_series_list and np.std(origin_series) > 0.0:
        try:
            granger_result = _compute_granger(
                origin_series, follower_series_list, max_lag=max_lag_g, alpha=alpha
            )
            if granger_result is None:
                rejection_parts.append("granger 计算返回空（常数/数值不稳定）")
        except Exception as exc:
            logger.info("granger_compute_failed", reason=str(exc))
            rejection_parts.append(f"granger 异常：{exc}")

    # QAP
    qap_result: QAPResult | None = None
    if follower_series_list and np.std(origin_series) > 0.0:
        try:
            qap_result = _qap_test(
                origin_series, follower_series_list,
                permutations=permutations, alpha=alpha,
            )
            if qap_result is None:
                rejection_parts.append("qap 计算返回空")
        except Exception as exc:
            logger.info("qap_failed", reason=str(exc))
            rejection_parts.append(f"qap 异常：{exc}")

    rejection_reason = "; ".join(rejection_parts) if rejection_parts else None

    logger.info(
        "stats_evidence_done",
        topic_id=str(topic_id),
        article_count=article_count,
        origin_country=origin_country,
        follower_count=len(follower_series_list),
        xcorr_significant=xcorr_result.significant if xcorr_result else None,
        granger_significant=granger_result.significant if granger_result else None,
        qap_significant=qap_result.significant if qap_result else None,
    )

    return StatsEvidence(
        article_count=article_count,
        xcorr=xcorr_result,
        granger=granger_result,
        qap=qap_result,
        insufficient_data=False,
        rejection_reason=rejection_reason,
    )
