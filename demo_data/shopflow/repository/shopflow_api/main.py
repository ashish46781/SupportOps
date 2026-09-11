from fastapi import FastAPI
from . import authentication, coupons, orders, payments, refunds, uploads

app = FastAPI()
app.include_router(orders.router)
app.include_router(payments.router)
app.include_router(refunds.router)
app.include_router(authentication.router)
app.include_router(uploads.router)
app.include_router(coupons.router)
