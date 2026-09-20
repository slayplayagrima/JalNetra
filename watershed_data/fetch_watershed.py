import json
import math
import ssl
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from shapely.geometry import Point, shape
import urllib3


# =========================================================
# SSL WARNING
# =========================================================

urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


# =========================================================
# LEGACY SSL ADAPTER
# GMDA server requires legacy TLS renegotiation
# =========================================================

class LegacySSLAdapter(HTTPAdapter):

    def init_poolmanager(
        self,
        connections,
        maxsize,
        block=False,
        **pool_kwargs
    ):

        context = ssl.create_default_context()

        # Allow legacy TLS renegotiation
        context.options |= ssl.OP_LEGACY_SERVER_CONNECT

        # GMDA certificate verification is disabled
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        pool_kwargs["ssl_context"] = context

        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs
        )


# Create one reusable session
session = requests.Session()

session.mount(
    "https://",
    LegacySSLAdapter()
)


# =========================================================
# PATHS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

INPUT_FILE = (
    BASE_DIR.parent
    / "weather_data"
    / "locations.csv"
)

OUTPUT_FILE = (
    BASE_DIR
    / "watershed_natural_flow.csv"
)


# =========================================================
# GMDA GIS URLs
# =========================================================

WATERSHED_URL = (
    "https://onemapdepts.gmda.gov.in/server/rest/services/"
    "flood_survey_2/FeatureServer/1/query"
)

FLOW_URL = (
    "https://onemapdepts.gmda.gov.in/server/rest/services/"
    "flood_survey_2/FeatureServer/6/query"
)


# =========================================================
# QUERY GMDA GIS LAYER
# =========================================================

def query_layer(
    url,
    lat,
    lon,
    distance=None
):

    geometry = json.dumps({
        "x": lon,
        "y": lat
    })

    params = {
        "where": "1=1",
        "geometry": geometry,
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": 4326,
        "f": "json"
    }

    if distance is not None:

        params["distance"] = distance
        params["units"] = "esriSRUnit_Meter"

    response = session.get(
        url,
        params=params,
        timeout=60,
        verify=False
    )

    response.raise_for_status()

    data = response.json()

    if "error" in data:

        raise RuntimeError(
            data["error"]
        )

    return data


# =========================================================
# ARCGIS GEOMETRY -> SHAPELY
# =========================================================

def arcgis_geometry_to_shapely(
    geometry,
    geometry_type
):

    if not geometry:
        return None

    # -----------------------------------------------------
    # Polygon
    # -----------------------------------------------------

    if geometry_type == "esriGeometryPolygon":

        rings = geometry.get(
            "rings",
            []
        )

        if not rings:
            return None

        return shape({
            "type": "Polygon",
            "coordinates": [rings[0]]
        })

    # -----------------------------------------------------
    # Polyline
    # -----------------------------------------------------

    if geometry_type == "esriGeometryPolyline":

        paths = geometry.get(
            "paths",
            []
        )

        if not paths:
            return None

        if len(paths) == 1:

            return shape({
                "type": "LineString",
                "coordinates": paths[0]
            })

        return shape({
            "type": "MultiLineString",
            "coordinates": paths
        })

    return None


# =========================================================
# DISTANCE IN METERS
# =========================================================

def calculate_meter_distance(
    point,
    geometry
):

    if geometry is None:
        return None

    nearest = geometry.interpolate(
        geometry.project(point)
    )

    lat = point.y

    meters_per_degree_lat = 111320

    meters_per_degree_lon = (
        111320
        * abs(
            math.cos(
                math.radians(lat)
            )
        )
    )

    dx = (
        nearest.x - point.x
    ) * meters_per_degree_lon

    dy = (
        nearest.y - point.y
    ) * meters_per_degree_lat

    return (
        (dx ** 2 + dy ** 2) ** 0.5
    )


# =========================================================
# PROCESS ONE LOCATION
# =========================================================

def process_location(row):

    location_id = row[
        "Location_ID"
    ]

    lat = float(
        row["Latitude"]
    )

    lon = float(
        row["Longitude"]
    )

    point = Point(
        lon,
        lat
    )

    # =====================================================
    # 1. WATERSHED
    # =====================================================

    watershed_data = query_layer(
        WATERSHED_URL,
        lat,
        lon
    )

    watershed_id = None

    watershed_boundary_distance = None

    watershed_features = (
        watershed_data.get(
            "features",
            []
        )
    )

    if watershed_features:

        feature = (
            watershed_features[0]
        )

        attrs = feature.get(
            "attributes",
            {}
        )

        watershed_id = attrs.get(
            "id"
        )

        geometry = (
            arcgis_geometry_to_shapely(
                feature.get(
                    "geometry"
                ),
                "esriGeometryPolygon"
            )
        )

        if geometry:

            watershed_boundary_distance = (
                calculate_meter_distance(
                    point,
                    geometry.boundary
                )
            )

    # =====================================================
    # 2. NATURAL FLOW
    # =====================================================

    flow_data = query_layer(
        FLOW_URL,
        lat,
        lon,
        distance=2000
    )

    nearest_flow = None

    nearest_flow_distance = None

    for feature in flow_data.get(
        "features",
        []
    ):

        geometry = (
            arcgis_geometry_to_shapely(
                feature.get(
                    "geometry"
                ),
                "esriGeometryPolyline"
            )
        )

        if geometry is None:
            continue

        distance = (
            calculate_meter_distance(
                point,
                geometry
            )
        )

        if (
            nearest_flow_distance is None
            or distance < nearest_flow_distance
        ):

            nearest_flow_distance = distance

            nearest_flow = feature

    # =====================================================
    # 3. BUILD RESULT
    # =====================================================

    result = {

        "Location_ID":
            location_id,

        "Watershed_ID":
            watershed_id,

        "Distance_to_Watershed_Boundary_m":
            (
                round(
                    watershed_boundary_distance,
                    1
                )
                if watershed_boundary_distance
                is not None
                else None
            ),

        "Natural_Flow_FID":
            None,

        "Natural_Flow_Stream_ID":
            None,

        "Natural_Flow_Elevation":
            None,

        "Natural_Flow_In_Flow":
            None,

        "Natural_Flow_Out_Flow":
            None,

        "Drain_Area":
            None,

        "Nearest_Natural_Flow_Distance_m":
            (
                round(
                    nearest_flow_distance,
                    1
                )
                if nearest_flow_distance
                is not None
                else None
            ),

        "Watershed_Source":
            (
                "GMDA flood_survey_2 "
                "(Watershed_Gurugram + "
                "Natural_Flow_Direction)"
            ),

        "Data_Quality":
            (
                "Official GMDA GIS; "
                "nearest-flow and boundary "
                "distances calculated from "
                "returned geometry"
            )
    }

    # =====================================================
    # 4. NATURAL FLOW ATTRIBUTES
    # =====================================================

    if nearest_flow:

        attrs = nearest_flow.get(
            "attributes",
            {}
        )

        result[
            "Natural_Flow_FID"
        ] = attrs.get(
            "objectid"
        )

        result[
            "Natural_Flow_Stream_ID"
        ] = attrs.get(
            "stream_id"
        )

        result[
            "Natural_Flow_Elevation"
        ] = attrs.get(
            "elevation"
        )

        result[
            "Natural_Flow_In_Flow"
        ] = attrs.get(
            "in_flow"
        )

        result[
            "Natural_Flow_Out_Flow"
        ] = attrs.get(
            "out_flow"
        )

        result[
            "Drain_Area"
        ] = attrs.get(
            "drain_area"
        )

    return result


# =========================================================
# MAIN
# =========================================================

def main():

    # =====================================================
    # LOAD LOCATIONS
    # =====================================================

    locations = pd.read_csv(
        INPUT_FILE,
        encoding="utf-8-sig"
    )

    locations.columns = (
        locations.columns
        .str.replace(
            "\ufeff",
            "",
            regex=False
        )
        .str.strip()
    )

    # Handle ID -> Location_ID
    if (
        "ID" in locations.columns
        and
        "Location_ID" not in locations.columns
    ):

        locations = locations.rename(
            columns={
                "ID": "Location_ID"
            }
        )

    # =====================================================
    # CHECK REQUIRED COLUMNS
    # =====================================================

    required = {
        "Location_ID",
        "Latitude",
        "Longitude"
    }

    missing = (
        required
        - set(locations.columns)
    )

    if missing:

        raise ValueError(
            f"Missing columns: {missing}. "
            f"Found: {list(locations.columns)}"
        )

    # =====================================================
    # PROCESS ALL LOCATIONS
    # =====================================================

    results = []

    total = len(
        locations
    )

    for index, row in locations.iterrows():

        location_id = (
            row["Location_ID"]
        )

        print(
            f"[{index + 1}/{total}] "
            f"Querying {location_id}..."
        )

        try:

            result = process_location(
                row
            )

            results.append(
                result
            )

            print(
                f"  OK: {location_id}"
            )

        except Exception as exc:

            print(
                f"  ERROR for "
                f"{location_id}: {exc}"
            )

    # =====================================================
    # CHECK RESULTS
    # =====================================================

    if not results:

        raise RuntimeError(
            "No watershed data was collected."
        )

    # =====================================================
    # SAVE CSV
    # =====================================================

    output = pd.DataFrame(
        results
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False
    )

    print()
    print("Done!")
    print(
        f"Rows collected: "
        f"{len(output)}"
    )

    print(
        f"Saved to: "
        f"{OUTPUT_FILE}"
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()