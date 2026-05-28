"""Pure-Python core library for property tooling.

This package contains reusable domain logic with minimal assumptions (no FastAPI,
no database/redis). The API service in `app/` wraps this package.
"""

from property_core.address_matching import match_epc_address
from property_core.block_service import analyze_blocks
from property_core.interpret import (
    classify_data_quality,
    classify_price_position,
    classify_yield,
    estimate_value_range,
    generate_insights,
)
from property_core.companies_house_client import CompaniesHouseClient
from property_core.epc_client import EPCClient
from property_core.enrichment import compute_enriched_stats, enrich_comps_with_epc
from property_core.models.block import BlockAnalysisResponse, BlockBuilding
from property_core.models.companies_house import CompanyRecord, CompanySearchResult
from property_core.models.epc import EPCData
from property_core.models.postcode import PostcodeResult
from property_core.models.ppd import PPDCompsResponse, PPDTransaction, PPDTransactionRecord
from property_core.models.report import PropertyReport, RentalAnalysis, YieldAnalysis
from property_core.models.rightmove import RightmoveListing, RightmoveListingDetail
from property_core.planning_service import PlanningService
from property_core.postcode_client import PostcodeClient
from property_core.ppd_client import PricePaidDataClient
from property_core.ppd_service import PPDService
from property_core.rental_service import analyze_rentals
from property_core.report_service import PropertyReportService
from property_core.stamp_duty import StampDutyResult, calculate_stamp_duty
from property_core.yield_service import calculate_yield
from property_core.rightmove_location import RightmoveLocationAPI
from property_core.rightmove_scraper import fetch_listing, fetch_listings
from property_core.models.newhomesforsale import (
    NewHomesForSaleDevelopment,
    NewHomesForSaleDevelopmentDetail,
)
from property_core.models.onthemarket import OnTheMarketListing, OnTheMarketListingDetail
from property_core.models.zoopla import ZooplaListing, ZooplaListingDetail
from property_core.newhomesforsale_location import NewHomesForSaleLocationAPI
from property_core.newhomesforsale_scraper import (
    fetch_listing as fetch_nhfs_listing,
    fetch_listings as fetch_nhfs_listings,
)
from property_core.newhomesforsale_service import filter_developments_by_distance
from property_core.location_resolution import (
    outcode_latlon,
    postcode_to_county,
    town_to_county,
)
from property_core.onthemarket_location import OnTheMarketLocationAPI
from property_core.onthemarket_scraper import (
    fetch_listing as fetch_onthemarket_listing,
    fetch_listings as fetch_onthemarket_listings,
)
from property_core.zoopla_location import ZooplaLocationAPI
from property_core.zoopla_scraper import (
    fetch_listing as fetch_zoopla_listing,
    fetch_listings as fetch_zoopla_listings,
)

__all__ = [
    # Services
    "CompaniesHouseClient",
    "EPCClient",
    "NewHomesForSaleLocationAPI",
    "OnTheMarketLocationAPI",
    "PlanningService",
    "PostcodeClient",
    "PPDService",
    "PricePaidDataClient",
    "PropertyReportService",
    "RightmoveLocationAPI",
    "ZooplaLocationAPI",
    # Functions
    "analyze_blocks",
    "analyze_rentals",
    "calculate_stamp_duty",
    "calculate_yield",
    "classify_data_quality",
    "classify_price_position",
    "classify_yield",
    "compute_enriched_stats",
    "enrich_comps_with_epc",
    "estimate_value_range",
    "filter_developments_by_distance",
    "fetch_listing",
    "fetch_listings",
    "fetch_nhfs_listing",
    "fetch_nhfs_listings",
    "fetch_onthemarket_listing",
    "fetch_onthemarket_listings",
    "fetch_zoopla_listing",
    "fetch_zoopla_listings",
    "generate_insights",
    "match_epc_address",
    "outcode_latlon",
    "postcode_to_county",
    "town_to_county",
    # Models
    "BlockAnalysisResponse",
    "BlockBuilding",
    "CompanyRecord",
    "CompanySearchResult",
    "EPCData",
    "NewHomesForSaleDevelopment",
    "NewHomesForSaleDevelopmentDetail",
    "OnTheMarketListing",
    "OnTheMarketListingDetail",
    "PostcodeResult",
    "PPDCompsResponse",
    "PPDTransaction",
    "PPDTransactionRecord",
    "PropertyReport",
    "RentalAnalysis",
    "RightmoveListing",
    "RightmoveListingDetail",
    "StampDutyResult",
    "YieldAnalysis",
    "ZooplaListing",
    "ZooplaListingDetail",
]
