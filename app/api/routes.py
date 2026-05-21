from fastapi import APIRouter

from .v1 import (
    analysis,
    companies_house,
    epc,
    health,
    meta,
    newhomesforsale,
    onthemarket,
    ppd,
    report,
    rightmove,
    stamp_duty,
    zoopla,
)
# from .v1 import planning  # Disabled: scraping requires UK residential IP

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(ppd.router)
api_router.include_router(epc.router)
api_router.include_router(rightmove.router)
api_router.include_router(zoopla.router)
api_router.include_router(onthemarket.router)
api_router.include_router(newhomesforsale.router)
# api_router.include_router(planning.router)  # Disabled: scraping requires UK residential IP
api_router.include_router(report.router)
api_router.include_router(meta.router)
api_router.include_router(stamp_duty.router)
api_router.include_router(companies_house.router)
api_router.include_router(analysis.router)
