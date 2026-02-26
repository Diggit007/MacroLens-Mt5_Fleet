import socketio
import asyncio
import logging
from backend.middleware.auth import verify_token

logger = logging.getLogger("WebSocket")

class WebSocketManager:
    def __init__(self):
        # Create Async Socket.IO Server
        self.sio = socketio.AsyncServer(
            async_mode='asgi',
            cors_allowed_origins=[
                'https://macrolens-ai.com', 
                'https://www.macrolens-ai.com', 
                'https://api.macrolens-ai.com',
                'https://macrolens-ai3.web.app', 
                'https://macrolens-ai3.firebaseapp.com',
                'http://localhost:5173', 
                'http://localhost:8000',
                'http://158.220.82.187:5173',
                'http://158.220.82.187:8000'
            ], 
            engineio_logger=False,
            logger=False,
            allow_upgrades=True,
            ping_timeout=60,
            ping_interval=25
        )
        self.app = socketio.ASGIApp(self.sio)
        
        # Track authenticated online users: { sid: user_id }
        self._connected_users: dict[str, str] = {}
        
        # Register Events
        self.sio.on('connect', self.handle_connect)
        self.sio.on('disconnect', self.handle_disconnect)
        self.sio.on('authenticate', self.handle_auth)

    async def handle_connect(self, sid, environ):
        """
        New Connection Interceptor.
        We can't easily read headers in WebSocket handshake with some clients,
        so we allow connection first, then wait for 'authenticate' event.
        """
        logger.info(f"ws_connect: {sid}")
        # Note: We don't join any rooms yet. User is effectively unauthenticated.
        return True

    async def handle_disconnect(self, sid):
        user_id = self._connected_users.pop(sid, None)
        if user_id:
            logger.info(f"ws_disconnect: {sid} (user: {user_id}) | Online: {self.online_count}")
            # Schedule Undeployment (Save Costs)
            # Find if this was the LAST connection for this user (user might have multiple tabs)
            if user_id not in self._connected_users.values():
                from backend.services.metaapi_service import schedule_undeploy_for_user
                await schedule_undeploy_for_user(user_id, delay_seconds=180) # 3 min delay
        else:
            logger.info(f"ws_disconnect: {sid} (unauthenticated)")

    async def handle_auth(self, sid, data):
        """
        Client sends { token: 'firebase-id-token' }
        Verify and add to user-specific room.
        """
        token = data.get('token')
        if not token:
            logger.warning(f"Auth Failed: No token for {sid}")
            await self.sio.emit('auth_error', {'msg': 'No token provided'}, room=sid)
            return

        try:
            decoded = verify_token(token)
            user_id = decoded.get('uid')
            
            if user_id:
                # Join Room named after User ID
                self.sio.enter_room(sid, user_id)
                self._connected_users[sid] = user_id
                logger.info(f"Auth Success: {sid} -> joined room {user_id} | Online: {self.online_count}")
                # Trigger Auto-Deploy
                from backend.services.metaapi_service import deploy_all_for_user
                
                # AWAIT Deployment to prevent Race Condition (Frontend subscribing before deployed)
                # This might delay login slightly but ensures robustness.
                try:
                    await deploy_all_for_user(user_id)
                except Exception as ex:
                    logger.error(f"Deploy failed impacting login: {ex}")
                
                # Send Auth Success ONLY after deployment logic runs
                await self.sio.emit('auth_success', {'user_id': user_id}, room=sid)
                
            else:
                logger.error(f"Auth Failed: Token valid but no UID for {sid}")
        except Exception as e:
            logger.error(f"Auth Exception: {e}")
            await self.sio.emit('auth_error', {'msg': str(e)}, room=sid)

    async def emit_update(self, user_id, data):
        """
        Emit account update to specific user room.
        Replacing push_update.
        """
        try:
            # Emit event 'account_update' to room {user_id}
            await self.sio.emit('account_update', data, room=user_id)
            # logger.debug(f"Emitted update to {user_id}")
        except Exception as e:
            logger.error(f"Emit Error for {user_id}: {e}")

    async def emit_analysis_progress(self, user_id, progress_data):
        """
        Emit analysis progress update to specific user room.
        Used for real-time progress bars during AI analysis.
        
        Args:
            user_id: Firebase user ID
            progress_data: Dict containing:
                - symbol: str
                - progress: int (0-100)
                - stage: str (description of current stage)
                - elapsed_ms: int
                - estimated_total_ms: int (optional)
        """
        try:
            await self.sio.emit('analysis_progress', progress_data, room=user_id)
            logger.debug(f"Analysis progress {progress_data['progress']}% sent to {user_id}")
        except Exception as e:
            logger.error(f"Analysis Progress Emit Error for {user_id}: {e}")

    async def emit_trade_manager_update(self, user_id: str, data: dict):
        """
        Emit Trade Manager update to specific user room.
        Used for real-time recommendations and autonomous action notifications.
        
        Args:
            user_id: Firebase user ID
            data: Dict containing:
                - type: str ("trade_manager_update")
                - recommendations: List of recommendation objects
        """
        try:
            await self.sio.emit('trade_manager_update', data, room=user_id)
            logger.info(f"Trade Manager update sent to {user_id}: {len(data.get('recommendations', []))} recommendations")
        except Exception as e:
            logger.error(f"Trade Manager Emit Error for {user_id}: {e}")

    @property
    def online_count(self) -> int:
        """Number of unique authenticated users currently connected."""
        return len(set(self._connected_users.values()))

    @property
    def online_user_ids(self) -> list[str]:
        """List of unique authenticated user IDs currently connected."""
        return list(set(self._connected_users.values()))

# Singleton Instance
websocket_manager = WebSocketManager()
