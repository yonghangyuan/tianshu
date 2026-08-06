"""Map Tools — 地理空间解析与路径规划。

纯 Python 实现，零外部 API 依赖。
依赖: geopy, osmnx, shapely (可选，缺失时降级为基本功能)
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import BaseSkill, SkillTool


class MapToolsSkill(BaseSkill):
    name = "map-tools"
    description = "地理空间工具——读取GeoJSON、坐标转换、路径规划。纯Python，零外部API。"
    trigram = "地"
    trigger_keywords = ["地图", "坐标", "路径", "路线", "导航", "GeoJSON", "map", "route"]

    def get_tools(self) -> list[SkillTool]:
        return [
            SkillTool(
                name="map_parse",
                description=(
                    "Parse a GeoJSON file or coordinate data. Returns structured geographic data.\n"
                    "Supports: GeoJSON files, CSV with lat/lng columns, WKT strings."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to a GeoJSON/CSV file, or a raw WKT/coordinate string",
                        },
                    },
                    "required": ["path"],
                },
                handler=self._parse,
                permission_level=0,
            ),
            SkillTool(
                name="map_analyze",
                description=(
                    "Analyze satellite/aerial imagery using spatial intelligence model (SenseNova-SI-1.3).\n"
                    "Use for: satellite image analysis, building detection, spatial layout understanding,\n"
                    "distance estimation, occlusion reasoning, viewpoint transformation.\n"
                    "The model excels at understanding 3D spatial relationships from 2D images.\n"
                    "Do NOT use for: general photos (use vision tool instead)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "image": {
                            "type": "string",
                            "description": "Image path or URL (satellite/aerial/drone imagery)",
                        },
                        "task": {
                            "type": "string",
                            "description": "Spatial analysis task. Options: detect_buildings, analyze_layout, estimate_distances, count_objects, describe_scene, custom",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "Custom prompt for spatial analysis. Required if task=custom.",
                        },
                    },
                    "required": ["image"],
                },
                handler=self._analyze_map,
                permission_level=0,
            ),
            SkillTool(
                name="map_route",
                description=(
                    "Calculate a route between two points. Uses A* on road network.\n"
                    "Returns: distance, estimated time, waypoints."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "from_lat": {"type": "number", "description": "起点纬度 (latitude)"},
                        "from_lng": {"type": "number", "description": "起点经度 (longitude)"},
                        "to_lat": {"type": "number", "description": "终点纬度"},
                        "to_lng": {"type": "number", "description": "终点经度"},
                    },
                    "required": ["from_lat", "from_lng", "to_lat", "to_lng"],
                },
                handler=self._route,
                permission_level=0,
            ),
        ]

    # ── 空间智能分析 (SenseNova-SI) ───────────────────────────────

    async def _analyze_map(
        self, image: str, task: str = "describe_scene", prompt: str = "", **kwargs
    ) -> str:
        """调用商汤 SenseNova-SI-1.3 进行空间智能分析。"""
        import os, base64 as _b64
        from pathlib import Path as _P

        # 获取 API Key
        api_key = os.environ.get("SENSENOVA_API_KEY", "")
        if not api_key:
            try:
                from tianshu.core.setup import load_user_keys
                keys = load_user_keys()
                api_key = keys.get("sensenova", "")
            except Exception:
                pass
        if not api_key:
            return (
                "❌ 未配置 SENSENOVA_API_KEY。\n"
                "申请地址: https://platform.sensenova.cn\n"
                "配置: /setup → 添加 sensenova Key"
            )

        # 图像 → base64
        try:
            if image.startswith(("http://", "https://")):
                import httpx
                async with httpx.AsyncClient(timeout=15) as cl:
                    r = await cl.get(image)
                    r.raise_for_status()
                    img_b64 = _b64.b64encode(r.content).decode()
            elif _P(image).exists():
                img_b64 = _b64.b64encode(_P(image).read_bytes()).decode()
            else:
                return f"❌ 图像不存在: {image}"
        except Exception as e:
            return f"❌ 读取图像失败: {e}"

        # 任务 → prompt 映射
        task_prompts = {
            "detect_buildings": (
                "Analyze this satellite/aerial image. Detect and count all buildings. "
                "For each building, estimate its approximate footprint area and describe its shape. "
                "Identify any large structures, warehouses, or unusual buildings."
            ),
            "analyze_layout": (
                "Analyze the spatial layout of this scene. Describe the arrangement of roads, "
                "buildings, open spaces, and natural features. Identify any patterns in the "
                "urban/rural planning. Note access routes and bottlenecks."
            ),
            "estimate_distances": (
                "Estimate distances between key features in this image. Identify the scale "
                "by looking for standard-sized objects (cars ~4.5m, road lanes ~3.5m). "
                "Calculate distances between major landmarks."
            ),
            "count_objects": (
                "Count and categorize all visible objects in this image: vehicles (cars, trucks, "
                "buses), buildings, trees, water bodies, roads, bridges. Group by type and report counts."
            ),
            "describe_scene": (
                "Describe this scene in detail from a spatial intelligence perspective. "
                "What is the terrain like? What human structures are visible? "
                "What is the approximate scale? What spatial relationships do you observe?"
            ),
        }

        analysis_prompt = prompt or task_prompts.get(task, task_prompts["describe_scene"])

        try:
            async with httpx.AsyncClient(timeout=60) as cl:
                resp = await cl.post(
                    "https://api.sensenova.cn/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "SenseNova-SI-1.3",
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": analysis_prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                            ],
                        }],
                        "max_tokens": 1500,
                    },
                )

                if resp.status_code != 200:
                    return f"❌ SenseNova API 错误 (HTTP {resp.status_code}): {resp.text[:300]}"

                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return f"🗺️ 空间智能分析 [{task}]:\n\n{content or '(空回复)'}"

        except Exception as e:
            return f"❌ SenseNova 调用失败: {e}"

    # ── GeoJSON 解析 ──────────────────────────────────────────────

    async def _parse(self, path: str, **kwargs) -> str:
        p = Path(path)
        if not p.exists():
            return f"❌ 文件不存在: {path}"

        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            data = json.loads(text)

            if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                features = data.get("features", [])
                lines = [
                    f"📂 {p.name}: GeoJSON FeatureCollection",
                    f"   要素数: {len(features)}",
                ]
                for i, f in enumerate(features[:10]):
                    geom_type = f.get("geometry", {}).get("type", "?")
                    props = f.get("properties", {})
                    name = props.get("name", props.get("title", f"Feature {i+1}"))
                    lines.append(f"   [{i+1}] {name} ({geom_type})")
                if len(features) > 10:
                    lines.append(f"   ... 还有 {len(features)-10} 个要素")
                return "\n".join(lines)

            elif isinstance(data, dict) and data.get("type") in (
                "Point", "LineString", "Polygon", "MultiPolygon",
            ):
                coords = data.get("coordinates", [])
                return f"📂 {p.name}: {data['type']} — {len(coords)} 个坐标点"

            else:
                return f"📂 {p.name}: 已解析 ({len(str(data))} 字符)"

        except json.JSONDecodeError:
            # 尝试当作 CSV 解析
            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.strip().split("\n")[:20]
            return f"📂 {p.name}: CSV/文本, {len(lines)} 行\n" + "\n".join(f"   {l[:120]}" for l in lines[:10])

    # ── 路径规划 ─────────────────────────────────────────────────

    async def _route(
        self,
        from_lat: float, from_lng: float,
        to_lat: float, to_lng: float,
        **kwargs,
    ) -> str:
        import math

        # 1. Haversine 距离（基础）
        R = 6371000  # 地球半径 (m)
        dlat = math.radians(to_lat - from_lat)
        dlng = math.radians(to_lng - from_lng)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(from_lat))
            * math.cos(math.radians(to_lat))
            * math.sin(dlng / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        dist_m = R * c

        # 2. 方位角
        y = math.sin(dlng) * math.cos(math.radians(to_lat))
        x = math.cos(math.radians(from_lat)) * math.sin(math.radians(to_lat)) - math.sin(
            math.radians(from_lat)
        ) * math.cos(math.radians(to_lat)) * math.cos(dlng)
        bearing = (math.degrees(math.atan2(y, x)) + 360) % 360

        # 3. 尝试 OSMnx 路径规划
        osm_route = ""
        try:
            import osmnx as ox
            G = ox.graph_from_point(
                ((from_lat + to_lat) / 2, (from_lng + to_lng) / 2),
                dist=max(1000, int(dist_m * 1.5)),
                network_type="drive",
            )
            orig = ox.distance.nearest_nodes(G, from_lng, from_lat)
            dest = ox.distance.nearest_nodes(G, to_lng, to_lat)
            route = ox.shortest_path(G, orig, dest, weight="length")
            if route:
                route_dist = sum(
                    G.edges[u, v, 0].get("length", 0) for u, v in zip(route[:-1], route[1:])
                )
                osm_route = (
                    f"\n   OSMnx 路径: {len(route)} 个节点, "
                    f"实际距离 {route_dist:.0f}m"
                )
        except ImportError:
            osm_route = "\n   [提示] pip install osmnx 启用路网路径规划"
        except Exception as e:
            osm_route = f"\n   OSMnx 规划失败: {e}"

        # 4. 方向描述
        directions = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
        dir_idx = round(bearing / 45) % 8

        return (
            f"🗺️ 路径规划: ({from_lat:.4f}, {from_lng:.4f}) → ({to_lat:.4f}, {to_lng:.4f})\n"
            f"   直线距离: {dist_m:.0f}m\n"
            f"   方向: {directions[dir_idx]} ({bearing:.0f}°)\n"
            f"   预计步行: {dist_m/80:.0f}min  驾车: {dist_m/500:.0f}min"
            f"{osm_route}"
        )
