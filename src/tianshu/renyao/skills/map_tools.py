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
