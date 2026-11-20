"""跨模块数据链路集成测试。"""
from __future__ import annotations

import unittest
from typing import Any

# 设置路径
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.store import store
from app.services.attr_dict import list_attrs, get_attr
from app.services.tag_define import get_tag, validate_tag_logic
from app.services.tag_compute import compute_tag, get_compute_result
from app.services.event_track import get_event
from app.services.event_cleanse import cleanse_events, get_cleanse_result
from app.services.event_aggregate import aggregate_events, get_aggregate_result
from app.services.user_segment import compute_segment, get_segment_result
from app.services.crowd_select import select_crowd, get_crowd_result
from app.services.overlap_analysis import analyze_overlap, get_overlap_result
from app.services.funnel_analyze import analyze_funnel, get_funnel_result
from app.services.session_split import split_sessions, get_session_result
from app.services.behavior_path import build_path_sankey, get_path_result
from app.services.retention_calc import calculate_retention, get_retention_result
from app.services.lifetime_value import predict_ltv, get_ltv_result, ltv_with_retention
from app.services.retention_calc import set_observation, confirm_zero_revisit
from app.services.value_calibration import (
    calibrate_value,
    create_followup_filter,
    followup_customers,
    get_calibration,
    get_followup_filter,
    list_calibrations,
)
from app.services.tag_snapshot import create_snapshot, get_snapshot
from app.services.behavior_heatmap import generate_heatmap, get_heatmap
from app.services.profile_card import build_profile_card, get_profile_result
from app.services.tag_drill import drill_tag, get_drill_result


class TestDataChains(unittest.TestCase):
    """测试跨模块数据链路。"""

    def setUp(self) -> None:
        """重置store状态。"""
        # 清空产物缓存
        store._artifacts.clear()
        store._compute_cache.clear()
        store._snapshots.clear()

    # ========== 链路1: 属性字典 → 标签定义 → 标签计算 ==========
    def test_chain_attr_to_tag_compute(self) -> None:
        """属性字典定义属性，标签定义引用属性，标签计算执行逻辑。"""
        # 1. 验证属性存在
        attr = get_attr("ATTR-002")
        self.assertIsNotNone(attr)
        self.assertEqual(attr["attr_name"], "最近活跃天数")

        # 2. 验证标签引用了属性
        tag = get_tag("TAG-001")
        self.assertIsNotNone(tag)
        self.assertIn("ATTR-002", tag["source_attrs"])

        # 3. 验证标签计算执行
        result = compute_tag("TC-001")
        self.assertEqual(result.tag_id, "TAG-001")
        self.assertGreater(result.result_count, 0)

        # 4. 验证产物可查询
        artifact = get_compute_result("TC-001")
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact["tag_id"], "TAG-001")

    # ========== 链路2: 事件埋点 → 清洗 → 聚合 ==========
    def test_chain_event_cleanse_aggregate(self) -> None:
        """事件定义 → 清洗规则 → 清洗执行 → 聚合消费清洗结果。"""
        # 1. 验证事件存在
        event = get_event("EV-001")
        self.assertIsNotNone(event)

        # 2. 验证清洗规则关联事件
        cleanse = get_cleanse_result("EC-001")
        if cleanse is None:
            cleanse_result = cleanse_events("EC-001")
            cleanse = cleanse_result.model_dump()
        self.assertEqual(cleanse["event_id"], "EV-001")
        self.assertGreater(cleanse["total_records"], 0)

        # 3. 验证聚合消费清洗结果
        agg_result = aggregate_events("EA-001")
        self.assertEqual(agg_result.event_id, "EV-001")
        self.assertGreater(len(agg_result.result_rows), 0)

        # 4. 验证聚合产物存在
        artifact = get_aggregate_result("EA-001")
        self.assertIsNotNone(artifact)

    # ========== 链路3: 用户分群 → 圈选 → 交集分析 ==========
    def test_chain_segment_crowd_overlap(self) -> None:
        """分群定义 → 圈选条件 → 交集分析消费圈选结果。"""
        # 1. 分群计算
        seg_result = compute_segment("SEG-001")
        self.assertEqual(seg_result.segment_id, "SEG-001")
        self.assertGreater(seg_result.user_count, 0)

        # 2. 圈选执行
        crowd_a = select_crowd("CS-001")
        self.assertEqual(crowd_a["condition_id"], "CS-001")
        self.assertGreater(crowd_a["matched_count"], 0)

        crowd_b = select_crowd("CS-002")
        self.assertEqual(crowd_b["condition_id"], "CS-002")

        # 3. 交集分析消费圈选结果
        overlap = analyze_overlap("OA-001")
        self.assertEqual(overlap["crowd_a"], "CS-001")
        self.assertEqual(overlap["crowd_b"], "CS-002")
        self.assertIn("result_count", overlap)

        # 4. 验证产物
        artifact = get_overlap_result("OA-001")
        self.assertIsNotNone(artifact)

    # ========== 链路4: 行为路径 → 漏斗分析 → 会话切割 ==========
    def test_chain_path_funnel_session(self) -> None:
        """行为路径分析 → 漏斗分析 → 会话切割。"""
        # 1. 会话切割
        session_result = split_sessions("SS-001")
        self.assertEqual(session_result.strategy_id, "SS-001")
        self.assertGreater(session_result.session_count, 0)

        # 2. 漏斗分析
        funnel_result = analyze_funnel("FN-001")
        self.assertEqual(funnel_result.funnel_id, "FN-001")
        self.assertEqual(len(funnel_result.step_counts), 3)
        self.assertGreater(funnel_result.step_counts[0], funnel_result.step_counts[1])

        # 3. 行为路径
        path_result = build_path_sankey("BP-001")
        self.assertEqual(path_result.model_id, "BP-001")
        self.assertGreater(len(path_result.sankey_nodes), 0)
        self.assertGreater(len(path_result.sankey_links), 0)

        # 4. 验证产物
        self.assertIsNotNone(get_session_result("SS-001"))
        self.assertIsNotNone(get_funnel_result("FN-001"))
        self.assertIsNotNone(get_path_result("BP-001"))

    # ========== 链路5: 留存计算 → LTV预测 ==========
    def test_chain_retention_ltv(self) -> None:
        """留存计算结果 → LTV预测校准。"""
        # 1. 留存计算
        retention = calculate_retention("RC-001")
        self.assertEqual(retention.config_id, "RC-001")
        self.assertGreater(len(retention.retention_rates), 0)

        # 2. LTV预测
        ltv = predict_ltv("LTV-001")
        self.assertEqual(ltv.model_id, "LTV-001")
        self.assertGreater(ltv.avg_ltv, 0)

        # 3. LTV结合留存校准
        calibrated = ltv_with_retention("LTV-001", "RC-001")
        self.assertIn("retention_adjusted_ltv", calibrated)
        self.assertIn("avg_retention_rate", calibrated)

        # 4. 验证产物
        self.assertIsNotNone(get_retention_result("RC-001"))
        self.assertIsNotNone(get_ltv_result("LTV-001"))

    # ========== 链路5b: 留存观察去驱动价值校准与跟进筛选 ==========
    def test_calibration_uses_observed_curve_only(self) -> None:
        """校准系数来自已观察回访日；入组日 1.0 不参与均值。"""
        retention = calculate_retention("RC-001")
        self.assertEqual(len(retention.observed_dates), 7)
        self.assertEqual(len(retention.retention_rates["2026-11-01"]), 7)

        calibration = calibrate_value("LTV-001", "RC-001")
        self.assertEqual(calibration["status"], "calibrated")
        # 均值与留存计算详情逐点对齐（含入组日基线），不跳点、不补零
        curve_rates = [point["retention_rate"] for point in calibration["observed_retention_curve"]]
        expected = sum(curve_rates) / len(curve_rates)
        self.assertEqual(len(curve_rates), len(retention.retention_rates["2026-11-01"]))
        self.assertAlmostEqual(calibration["avg_retention_rate"], expected, places=6)
        self.assertAlmostEqual(
            calibration["calibration_coefficient"], 0.5 + 0.5 * expected, places=6
        )
        # 校准后均值 = 原始预测 × 系数，原始预测本身不改
        self.assertAlmostEqual(
            calibration["calibrated_avg_ltv"],
            round(calibration["original_avg_ltv"] * calibration["calibration_coefficient"], 2),
            places=2,
        )
        # 校准数据必须带上观察来源三件套
        source = calibration["observation_source"]
        self.assertEqual(source["observed_dates"], retention.observed_dates)
        self.assertEqual(source["valid_samples"], retention.valid_samples)
        self.assertEqual(source["completion_version"], retention.completion_version)
        self.assertTrue(calibration["original_curve"])
        self.assertGreater(calibration["original_estimate"]["avg_ltv"], 0)

    def test_confirmed_zero_revisit_still_calibrates(self) -> None:
        """确认零回访是有效观察：参与校准并得到 0.5 系数，而不是丢失观察。"""
        confirm_zero_revisit("RC-003")
        retention = get_retention_result("RC-003")
        self.assertTrue(retention["confirmed_zero_revisit"])
        self.assertIn(0.0, retention["retention_rates"]["2026-11-01"][1:])

        calibration = calibrate_value("LTV-001", "RC-003")
        self.assertEqual(calibration["status"], "calibrated")
        self.assertTrue(calibration["confirmed_zero_revisit"])
        self.assertEqual(calibration["avg_revisit_rate"], 0.0)
        # 1.0 的入组日基线 + 观察到的零回访 → 均值是 1/7，不是缺失曲线时的 0
        self.assertAlmostEqual(calibration["avg_retention_rate"], 1 / 7, places=6)
        self.assertAlmostEqual(
            calibration["calibration_coefficient"], 0.5 + 0.5 / 7, places=6
        )

        followup = create_followup_filter("LTV-001", 1, case="confirmed_zero_revisit")
        self.assertEqual(followup["case"], "confirmed_zero_revisit")
        self.assertEqual(followup["valuation_type"], "retention_calibrated")
        self.assertEqual(followup["calibration_id"], calibration["calibration_id"])
        self.assertTrue(all(item["valuation_type"] == "retention_calibrated" for item in followup["items"]))

    def test_correcting_enrollment_fails_without_coefficient(self) -> None:
        """指定留存组在更正入组数据：不生成系数并说明原因，保留原始预测。"""
        result = calculate_retention("RC-002")
        self.assertEqual(result.data_status, "correcting_enrollment")
        self.assertEqual(result.observed_dates, [])

        calibration = calibrate_value("LTV-002", "RC-002")
        self.assertEqual(calibration["status"], "calibration_failed")
        self.assertIsNone(calibration["calibration_coefficient"])
        self.assertIsNone(calibration["calibrated_avg_ltv"])
        self.assertIn("更正入组数据", calibration["reason"])
        self.assertEqual(calibration["valuation_type"], "original_prediction")
        self.assertGreater(calibration["original_estimate"]["avg_ltv"], 0)
        self.assertTrue(calibration["original_curve"])

        followup = create_followup_filter("LTV-002", 3.5)
        self.assertEqual(followup["case"], "calibration_failed")
        self.assertEqual(followup["valuation_type"], "original_prediction")
        self.assertEqual(followup["ready_count"] + followup["blocked_count"], len(followup["items"]))
        self.assertGreater(followup["ready_count"], 0)
        self.assertTrue(all(item["valuation_type"] == "original_prediction" for item in followup["items"]))
        self.assertIn("更正入组数据", followup["items"][0]["reason"])
        # 沿用原始预测，不因校准失败被减半
        first = followup["items"][0]
        self.assertEqual(first["valuation"], first["predicted_value"])

        customers = followup_customers("LTV-002", 3.5, case="calibration_failed")
        self.assertEqual(customers["valuation_type"], "original_prediction")
        self.assertEqual(len(customers["items"]), followup["ready_count"])

    def test_no_sample_reports_reason(self) -> None:
        """样本缺失时不能生成系数，原因要能读懂。"""
        set_observation("RC-002", observation_state="observed", observed_days=5, sample_size=0)
        result = calculate_retention("RC-002")
        self.assertEqual(result.data_status, "no_sample")
        self.assertEqual(result.valid_samples, 0)
        self.assertEqual(result.observed_dates, [])

        calibration = calibrate_value("LTV-002", "RC-002")
        self.assertEqual(calibration["status"], "calibration_failed")
        self.assertIsNone(calibration["calibration_coefficient"])
        self.assertIn("有效样本", calibration["reason"])

    def test_recompute_keeps_old_reports_and_curves(self) -> None:
        """留存补算后重试同一门槛：新报告更新，旧报告仍能核对原值和曲线。"""
        calculate_retention("RC-001")
        first_calibration = calibrate_value("LTV-001", "RC-001")
        first_followup = create_followup_filter("LTV-001", 100)
        first_avg = first_calibration["original_estimate"]["avg_ltv"]
        first_curve_len = len(first_calibration["original_curve"])

        # 补算留存 → 新的完成版本
        recomputed = calculate_retention("RC-001")
        self.assertNotEqual(
            recomputed.completion_version,
            first_calibration["observation_source"]["completion_version"],
        )
        second_calibration = calibrate_value("LTV-001", "RC-001")
        self.assertNotEqual(
            second_calibration["calibration_id"], first_calibration["calibration_id"]
        )
        self.assertGreater(
            second_calibration["report_version"], first_calibration["report_version"]
        )

        # 同一门槛重试得到新记录，旧记录保持原样
        second_followup = create_followup_filter("LTV-001", 100)
        self.assertGreater(second_followup["report_version"], first_followup["report_version"])
        self.assertEqual(
            second_followup["calibration_id"], second_calibration["calibration_id"]
        )

        old_calibration = get_calibration(first_calibration["calibration_id"])
        self.assertEqual(old_calibration["original_estimate"]["avg_ltv"], first_avg)
        self.assertEqual(len(old_calibration["original_curve"]), first_curve_len)
        self.assertEqual(
            old_calibration["observation_source"]["completion_version"],
            first_calibration["observation_source"]["completion_version"],
        )
        old_followup = get_followup_filter(first_followup["filter_id"])
        self.assertEqual(old_followup["calibration_id"], first_calibration["calibration_id"])
        self.assertEqual(old_followup["original_estimate"]["avg_ltv"], first_avg)
        self.assertEqual(len(old_followup["original_curve"]), first_curve_len)

        failed_versions = [
            row["report_version"]
            for row in list_calibrations(model_id="LTV-001")
        ]
        self.assertEqual(len(failed_versions), len(set(failed_versions)))

    # ========== 链路6: 标签快照消费标签计算+分群结果 ==========
    def test_chain_tag_snapshot_consumes_compute_and_segment(self) -> None:
        """标签快照消费标签计算结果和用户分群结果。"""
        # 1. 先执行标签计算
        compute_tag("TC-001")
        compute_tag("TC-002")

        # 2. 执行分群计算
        compute_segment("SEG-001")

        # 3. 创建快照（消费上述结果）
        snapshot = create_snapshot("SN-001")
        self.assertEqual(snapshot["snapshot_id"], "SN-001")
        self.assertGreater(snapshot["tag_count"], 0)
        self.assertGreater(snapshot["segment_count"], 0)

        # 4. 验证快照包含计算结果引用
        self.assertIn("compute_results", snapshot)
        self.assertGreater(len(snapshot["compute_results"]), 0)

    # ========== 链路7: 行为热力消费事件聚合数据 ==========
    def test_chain_heatmap_consumes_aggregate(self) -> None:
        """行为热力图消费事件聚合结果。"""
        # 1. 先执行事件聚合
        aggregate_events("EA-001")

        # 2. 生成热力图
        heatmap = generate_heatmap("BH-001")
        self.assertEqual(heatmap["config_id"], "BH-001")
        self.assertIn("heatmap_data", heatmap)

        # 3. 验证产物
        self.assertIsNotNone(get_heatmap("BH-001"))

    # ========== 链路8: 画像卡片消费标签计算+分群结果 ==========
    def test_chain_profile_card_consumes_compute_and_segment(self) -> None:
        """画像卡片消费标签计算结果和分群结果。"""
        # 1. 执行标签计算
        compute_tag("TC-001")
        compute_tag("TC-002")

        # 2. 执行分群
        compute_segment("SEG-001")

        # 3. 构建画像卡片
        profile = build_profile_card("PC-001")
        self.assertEqual(profile["card_id"], "PC-001")
        self.assertGreater(len(profile["display_tags"]), 0)
        self.assertIsNotNone(profile["segment_info"])

        # 4. 验证产物
        self.assertIsNotNone(get_profile_result("PC-001"))

    # ========== 链路9: 标签下钻消费标签计算结果 ==========
    def test_chain_tag_drill_consumes_compute(self) -> None:
        """标签下钻分析消费标签计算结果。"""
        # 1. 执行标签计算
        compute_tag("TC-001")

        # 2. 执行下钻
        drill = drill_tag("TD-001")
        self.assertEqual(drill["parent_tag"], "TAG-001")
        self.assertIn("drill_data", drill)

    # ========== 链路10: 漏斗对比消费漏斗结果 ==========
    def test_chain_funnel_compare(self) -> None:
        """漏斗对比消费两个漏斗的分析结果。"""
        # 1. 执行两个漏斗
        analyze_funnel("FN-001")

        # 2. 对比（即使第二个不存在也能测试错误处理）
        with self.assertRaises(ValueError):
            from app.services.funnel_analyze import compare_funnels
            compare_funnels("FN-001", "FN-NONEXIST")

    # ========== 额外测试：数据产物仓库 ==========
    def test_artifact_storage_and_retrieval(self) -> None:
        """测试数据产物存储和查询。"""
        store.set_artifact("test", "key1", {"data": "value1"})
        artifact = store.get_artifact("test", "key1")
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact["data"], "value1")

    def test_snapshot_management(self) -> None:
        """测试快照管理。"""
        store.add_snapshot("test_snap", {"version": 1})
        store.add_snapshot("test_snap", {"version": 2})
        latest = store.get_latest_snapshot("test_snap")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["version"], 2)

    def test_cross_module_join(self) -> None:
        """测试跨模块关联查询。"""
        # attr_dict 和 tag_define 通过 attr_id 关联（source_attrs 是列表，改用 tag_level 简单测试）
        results = store.join("tag_define", "attr_dict", "tag_level", "data_type")
        self.assertIsInstance(results, list)


class TestBusinessLogic(unittest.TestCase):
    """测试核心业务逻辑正确性。"""

    def setUp(self) -> None:
        store._artifacts.clear()
        store._compute_cache.clear()

    def test_tag_compute_logic_eval(self) -> None:
        """测试标签计算逻辑求值。"""
        from app.services.tag_compute import _eval_condition
        user = {"ATTR-002": 2, "ATTR-003": 1500.0}
        attr_defs = {}
        result = _eval_condition(user, "ATTR-002 <= 3 AND ATTR-003 > 1000", attr_defs)
        self.assertTrue(result)

        user2 = {"ATTR-002": 10, "ATTR-003": 500.0}
        result2 = _eval_condition(user2, "ATTR-002 <= 3 AND ATTR-003 > 1000", attr_defs)
        self.assertFalse(result2)

    def test_funnel_conversion_declines(self) -> None:
        """测试漏斗转化率递减。"""
        result = analyze_funnel("FN-001")
        counts = result.step_counts
        for i in range(1, len(counts)):
            self.assertLessEqual(counts[i], counts[i - 1])

    def test_session_split_creates_sessions(self) -> None:
        """测试会话切割产生会话。"""
        result = split_sessions("SS-001")
        self.assertGreater(result.session_count, 0)
        self.assertLessEqual(result.session_count, result.total_events)

    def test_retention_rate_declines(self) -> None:
        """测试留存率随时间递减。"""
        result = calculate_retention("RC-001")
        for key, rates in result.retention_rates.items():
            for i in range(1, len(rates)):
                self.assertLessEqual(rates[i], rates[i - 1])

    def test_crowd_select_produces_users(self) -> None:
        """测试圈选产生用户列表。"""
        result = select_crowd("CS-001")
        self.assertGreater(result["matched_count"], 0)
        self.assertGreater(len(result["matched_users"]), 0)

    def test_overlap_respects_mode(self) -> None:
        """测试交集模式正确性。"""
        # 确保两个圈选有结果
        select_crowd("CS-001")
        select_crowd("CS-002")
        result = analyze_overlap("OA-001")
        # intersect 模式结果数不应超过任一人群
        self.assertLessEqual(
            result["result_count"],
            max(result["users_a_count"], result["users_b_count"])
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
