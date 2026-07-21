import httpx
from fastapi import HTTPException, status

URL = "http://156.67.25.93:8006/v1/watchTower/GetWatchTowerPubKey"
WATCHTOWER_TOKEN = "isYBQGmwgycIA3CjD5V7rTs5qHj30WFtBKezWUEXyJCeZN44OrN8vDm/jgQNlMyUbdZY7rBtcVNPeFyajc5RBQ==.yjtrpozx43lknu6ghw4f7i5j"

async def test_token_verification():
    try:
        headers = {
            "Content-Type": "application/json",
            "watchtower-token": WATCHTOWER_TOKEN,
        }
        response = httpx.get(URL, headers=headers)

        print(response.json())
        response.raise_for_status()
        
        success = response.json().get("isSuccess", False)
        if not success:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid watchtower token")
        public_key_pem = response.json().get("pub_cov")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid watchtower token: {e}")
    return public_key_pem


if __name__ == "__main__":
    import asyncio
    public_key_pem = asyncio.run(test_token_verification())
    print(public_key_pem)
