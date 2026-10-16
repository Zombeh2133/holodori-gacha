from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_session
from ..models import GameAccount, GachaBanner, GachaPull

router=APIRouter(prefix="/api/admin",tags=["admin"])
ADMIN_DISCORD_USER_ID="1389811257577705573"

def _require_admin(request:Request)->None:
    did=str(request.session.get("discord_user_id") or "").strip()
    if not did: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="Not signed in")
    if did!=ADMIN_DISCORD_USER_ID: raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Admin access required")

@router.get("/overview")
async def overview(request:Request,page:int=Query(1,ge=1),page_size:int=Query(50,ge=10,le=100),session:AsyncSession=Depends(get_session))->dict:
    _require_admin(request)
    total=int(await session.scalar(select(func.count(GachaPull.id))) or 0)
    users=int(await session.scalar(select(func.count(func.distinct(GameAccount.owner_identity_id)))) or 0)
    accounts=int(await session.scalar(select(func.count(GameAccount.id))) or 0)
    rarity={r:int(await session.scalar(select(func.count(GachaPull.id)).where(GachaPull.rarity==r)) or 0) for r in (3,4,5)}
    regions=(await session.execute(select(GameAccount.region,func.count(func.distinct(GameAccount.id)),func.count(GachaPull.id)).outerjoin(GachaPull,GachaPull.game_account_id==GameAccount.id).group_by(GameAccount.region).order_by(GameAccount.region))).all()
    pages=max(1,(total+page_size-1)//page_size); safe=min(page,pages); offset=(safe-1)*page_size
    rows=(await session.execute(select(GachaPull,GameAccount,GachaBanner).join(GameAccount,GameAccount.id==GachaPull.game_account_id).join(GachaBanner,GachaBanner.id==GachaPull.banner_id).order_by(GachaPull.pulled_at.desc().nullslast(),GachaPull.created_at.desc(),GachaPull.pull_order.desc()).offset(offset).limit(page_size))).all()
    return {"summary":{"total_pulls":total,"tracked_users":users,"game_accounts":accounts,"five_star_pulls":rarity[5],"four_star_pulls":rarity[4],"three_star_pulls":rarity[3]},"regions":[{"region":str(r),"accounts":int(a or 0),"pulls":int(p or 0)} for r,a,p in regions],"pulls":{"page":safe,"page_size":page_size,"total":total,"total_pages":pages,"rows":[{"id":str(p.id),"item_id":p.item_id,"item_name":p.item_name,"rarity":int(p.rarity),"pulled_at":p.pulled_at.isoformat() if p.pulled_at else None,"region":a.region,"game_account_id":a.public_user_id,"nickname":a.nickname,"banner_name":b.name} for p,a,b in rows]}}
