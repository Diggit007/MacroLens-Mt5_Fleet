import logging

logger = logging.getLogger("MetaAPIClientShim")

# A shim to completely replace the metaapi_cloud_sdk dependency
# while maintaining the exact same class/method structures expected by the rest of the backend.

class DummyConnection:
    def __init__(self, account_id):
        self.account_id = account_id

    async def connect(self): pass
    async def wait_synchronized(self): pass
    async def close(self): pass

    async def get_account_information(self):
        # Lazy import to avoid circular dependency
        from backend.services.metaapi_service import get_account_information as get_info
        res = await get_info(self.account_id)
        return res

    async def get_positions(self):
        from backend.services.metaapi_service import get_account_information as get_info
        res = await get_info(self.account_id)
        return res.get("positions", [])

class DummyAccount:
    def __init__(self, account_id):
        self.account_id = account_id
        self.state = 'DEPLOYED'
        self.connection_status = 'CONNECTED'
        
    def get_rpc_connection(self):
        return DummyConnection(self.account_id)
        
    async def deploy(self): pass
    async def wait_connected(self): pass

class MetaAPIClientShim:
    def __init__(self):
        self.metatrader_account_api = self  # for api.metatrader_account_api.get_account

    async def get_account(self, account_id: str):
        return DummyAccount(account_id)
        
    async def get_rpc_connection(self, account_id: str):
        return DummyConnection(account_id)
        
    async def get_billing_info(self):
        return {
            "success": True,
            "balance": 0,
            "activeAccounts": 18, # Fallback static
            "equity": 0,
            "credit": 9999,
            "creditsRemaining": 9999,
            "monthlyRate": 0,
            "note": "Self-Hosted Fleet Manager"
        }
        
    def get_instance(self):
        return self

meta_api_singleton = MetaAPIClientShim()
