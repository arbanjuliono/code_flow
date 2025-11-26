import os
import time
import json
import logging
from typing import Dict, Any, Optional, List

from web3 import Web3
from web3.exceptions import BlockNotFound
from web3.middleware import geth_poa_middleware
from dotenv import load_dotenv

# --- Configuration Loading ---
load_dotenv()

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(module)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- Constants and ABIs ---
# In a real-world scenario, this ABI would be loaded from a file.
# This is a simplified ABI for a hypothetical bridge contract.
BRIDGE_CONTRACT_ABI = json.dumps([
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "sender", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "destinationChainId", "type": "uint256"},
            {"indexed": True, "internalType": "address", "name": "recipient", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount", "type": "uint256"},
            {"indexed": True, "internalType": "uint256", "name": "nonce", "type": "uint256"}
        ],
        "name": "TokensDeposited",
        "type": "event"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "recipient", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
            {"internalType": "uint256", "name": "nonce", "type": "uint256"}
        ],
        "name": "withdrawTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
])

# Configuration parameters loaded from environment variables
SOURCE_CHAIN_RPC = os.getenv('SOURCE_CHAIN_RPC')
DEST_CHAIN_RPC = os.getenv('DEST_CHAIN_RPC')
SOURCE_BRIDGE_CONTRACT = os.getenv('SOURCE_BRIDGE_CONTRACT')
DEST_BRIDGE_CONTRACT = os.getenv('DEST_BRIDGE_CONTRACT')
RELAYER_PRIVATE_KEY = os.getenv('RELAYER_PRIVATE_KEY')

class ChainConnector:
    """
    A generic class to handle connections to a blockchain via an RPC endpoint.
    It encapsulates a Web3.py instance and provides basic connectivity methods.
    """
    def __init__(self, chain_id: int, rpc_url: str):
        """
        Initializes the connector for a specific chain.
        
        Args:
            chain_id (int): The ID of the chain (e.g., 1 for Ethereum Mainnet).
            rpc_url (str): The HTTP RPC endpoint URL for the chain's node.
        """
        self.chain_id = chain_id
        self.rpc_url = rpc_url
        if not self.rpc_url:
            raise ValueError(f"RPC URL for chain {chain_id} is not configured.")
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        # Inject middleware for PoA chains like Goerli, Sepolia, Polygon
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

    def is_connected(self) -> bool:
        """Checks if the connection to the blockchain node is active."""
        try:
            return self.w3.is_connected()
        except Exception as e:
            logger.error(f"[Chain {self.chain_id}] Connection check failed: {e}")
            return False

    def get_latest_block_number(self) -> int:
        """Fetches the latest block number from the chain."""
        if not self.is_connected():
            raise ConnectionError(f"Not connected to chain {self.chain_id}.")
        return self.w3.eth.block_number

    def get_block_hash(self, block_number: int) -> Optional[str]:
        """Fetches the hash of a specific block."""
        try:
            block = self.w3.eth.get_block(block_number)
            return block.hash.hex()
        except BlockNotFound:
            logger.warning(f"[Chain {self.chain_id}] Block {block_number} not found.")
            return None
        except Exception as e:
            logger.error(f"[Chain {self.chain_id}] Error fetching block {block_number}: {e}")
            return None

class SourceChainListener:
    """
    Listens for specific events on the source chain's bridge contract.
    It scans blocks for `TokensDeposited` events.
    """
    def __init__(self, connector: ChainConnector, contract_address: str):
        """
        Initializes the listener.

        Args:
            connector (ChainConnector): The connector for the source chain.
            contract_address (str): The address of the bridge contract to listen to.
        """
        self.connector = connector
        if not self.connector.w3.is_address(contract_address):
            raise ValueError(f"Invalid source contract address: {contract_address}")
        self.contract = self.connector.w3.eth.contract(
            address=contract_address,
            abi=BRIDGE_CONTRACT_ABI
        )
        logger.info(f"[Source Chain] Initialized listener for contract at {contract_address}")

    def scan_for_events(self, from_block: int, to_block: int) -> List[Dict[str, Any]]:
        """
        Scans a range of blocks for 'TokensDeposited' events.

        Args:
            from_block (int): The starting block number for the scan.
            to_block (int): The ending block number for the scan.

        Returns:
            List[Dict[str, Any]]: A list of processed event data dictionaries.
        """
        if from_block > to_block:
            return []

        logger.info(f"[Source Chain] Scanning for events from block {from_block} to {to_block}.")
        try:
            event_filter = self.contract.events.TokensDeposited.create_filter(
                fromBlock=from_block,
                toBlock=to_block
            )
            events = event_filter.get_all_entries()
            processed_events = [self._process_event_log(event) for event in events]
            if processed_events:
                logger.info(f"[Source Chain] Found {len(processed_events)} new events.")
            return processed_events
        except Exception as e:
            logger.error(f"[Source Chain] Error while scanning for events: {e}")
            return []

    def _process_event_log(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Formats a raw event log into a structured dictionary."""
        return {
            'tx_hash': event['transactionHash'].hex(),
            'block_number': event['blockNumber'],
            'sender': event['args']['sender'],
            'destination_chain_id': event['args']['destinationChainId'],
            'recipient': event['args']['recipient'],
            'amount': event['args']['amount'],
            'nonce': event['args']['nonce']
        }

class DestinationChainProcessor:
    """
    Processes withdrawal requests on the destination chain.
    It simulates building, signing, and sending transactions to fulfill deposits from the source chain.
    """
    def __init__(self, connector: ChainConnector, contract_address: str, relayer_private_key: str):
        """
        Initializes the processor.

        Args:
            connector (ChainConnector): The connector for the destination chain.
            contract_address (str): The address of the bridge contract on the destination chain.
            relayer_private_key (str): The private key of the relayer account used to send transactions.
        """
        self.connector = connector
        if not self.connector.w3.is_address(contract_address):
            raise ValueError(f"Invalid destination contract address: {contract_address}")
        self.contract = self.connector.w3.eth.contract(
            address=contract_address,
            abi=BRIDGE_CONTRACT_ABI
        )
        try:
            self.relayer_account = self.connector.w3.eth.account.from_key(relayer_private_key)
            self.relayer_address = self.relayer_account.address
            logger.info(f"[Dest Chain] Initialized processor with relayer address: {self.relayer_address}")
        except Exception as e:
            logger.critical(f"[Dest Chain] Invalid relayer private key provided. Error: {e}")
            raise ValueError("Invalid relayer private key.")

    def process_withdrawal(self, event_data: Dict[str, Any]) -> bool:
        """
        Simulates the process of creating and sending a withdrawal transaction.

        Args:
            event_data (Dict[str, Any]): The processed event data from the source chain.

        Returns:
            bool: True if the simulation was successful, False otherwise.
        """
        try:
            logger.info(f"[Dest Chain] Processing withdrawal for nonce {event_data['nonce']} to {event_data['recipient']}.")
            
            # 1. Build the transaction
            tx_params = {
                'from': self.relayer_address,
                'nonce': self.connector.w3.eth.get_transaction_count(self.relayer_address),
                'gas': 200000, # A placeholder gas limit
                'gasPrice': self.connector.w3.eth.gas_price,
                'chainId': self.connector.chain_id
            }
            
            unsigned_tx = self.contract.functions.withdrawTokens(
                event_data['recipient'],
                event_data['amount'],
                event_data['nonce']
            ).build_transaction(tx_params)

            # 2. Sign the transaction
            signed_tx = self.connector.w3.eth.account.sign_transaction(unsigned_tx, self.relayer_account.key)

            # 3. Send the transaction (SIMULATION)
            # In a real system, you would use: self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            # Here, we just log the action to avoid sending a real transaction.
            tx_hash_simulation = signed_tx.hash.hex()
            logger.info(f"[Dest Chain] SIMULATED sending withdrawal tx. Hash: {tx_hash_simulation}")
            
            # Simulate waiting for receipt (in a real scenario, this would be a blocking call)
            time.sleep(1) 
            logger.info(f"[Dest Chain] SIMULATED withdrawal for nonce {event_data['nonce']} successful.")

            return True
        except Exception as e:
            logger.error(f"[Dest Chain] Failed to process withdrawal for nonce {event_data['nonce']}. Error: {e}")
            return False

class BridgeOrchestrator:
    """
    The main orchestrator that manages the listener and processor.
    It contains the main loop, handles state persistence, and manages resilience patterns like re-org handling.
    """
    STATE_FILE = 'bridge_state.json'
    REORG_CONFIRMATION_DEPTH = 10 # Number of blocks to wait before considering a block final

    def __init__(self, source_listener: SourceChainListener, dest_processor: DestinationChainProcessor):
        self.source_listener = source_listener
        self.dest_processor = dest_processor
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        """Loads the last saved state from a file. If no state exists, it creates a default one."""
        try:
            if os.path.exists(self.STATE_FILE):
                with open(self.STATE_FILE, 'r') as f:
                    state = json.load(f)
                    logger.info(f"Loaded state from {self.STATE_FILE}: {state}")
                    return state
        except (IOError, json.JSONDecodeError) as e:
            logger.warning(f"Could not load state file. Starting fresh. Error: {e}")
        
        # Default state if file doesn't exist or is corrupt
        return {
            'last_scanned_block': self.source_listener.connector.get_latest_block_number() - self.REORG_CONFIRMATION_DEPTH,
            'last_scanned_block_hash': None,
            'processed_nonces': []
        }

    def _persist_state(self):
        """Saves the current state to the state file."""
        try:
            with open(self.STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=4)
            logger.debug(f"Persisted state: {self.state}")
        except IOError as e:
            logger.error(f"Failed to persist state to {self.STATE_FILE}: {e}")

    def _handle_reorg(self) -> bool:
        """
        Checks for chain re-organizations on the source chain.
        It compares the saved hash of the last scanned block with the current chain.

        Returns:
            bool: True if a re-org was detected and handled, False otherwise.
        """
        last_hash = self.state.get('last_scanned_block_hash')
        last_block_num = self.state.get('last_scanned_block')

        if not last_hash or not last_block_num:
            return False # Nothing to check against

        current_hash = self.source_listener.connector.get_block_hash(last_block_num)
        
        if current_hash is None: # Block might not exist anymore
             logger.warning(f"Re-org detected! Block {last_block_num} is no longer in the canonical chain.")
             self.state['last_scanned_block'] = last_block_num - self.REORG_CONFIRMATION_DEPTH
             self.state['processed_nonces'] = [n for n in self.state['processed_nonces'] if n['block_number'] < self.state['last_scanned_block']]
             logger.info(f"Rolled back state to block {self.state['last_scanned_block']}")
             return True
        elif current_hash != last_hash:
            logger.warning(f"Re-org detected! Hash mismatch for block {last_block_num}.")
            self.state['last_scanned_block'] = last_block_num - self.REORG_CONFIRMATION_DEPTH
            # Prune nonces from blocks that may have been re-orged
            self.state['processed_nonces'] = [n for n in self.state['processed_nonces'] if n['block_number'] < self.state['last_scanned_block']]
            logger.info(f"Rolled back state to block {self.state['last_scanned_block']}")
            return True

        return False

    def run(self):
        """The main execution loop for the orchestrator."""
        logger.info("--- Bridge Orchestrator Starting Up ---")
        while True:
            try:
                # Health check connections
                if not self.source_listener.connector.is_connected() or not self.dest_processor.connector.is_connected():
                    logger.error("A chain connection is down. Retrying in 60 seconds...")
                    time.sleep(60)
                    continue

                # Re-org check before processing
                if self._handle_reorg():
                    self._persist_state()
                    continue

                # Determine block range to scan
                latest_block = self.source_listener.connector.get_latest_block_number()
                from_block = self.state['last_scanned_block'] + 1
                # Process in chunks and leave a confirmation buffer
                to_block = min(from_block + 100, latest_block - self.REORG_CONFIRMATION_DEPTH)

                if from_block > to_block:
                    logger.info(f"Waiting for new blocks to be confirmed... Current head: {latest_block}")
                    time.sleep(15) # Wait for new blocks
                    continue
                
                # Scan for events
                events = self.source_listener.scan_for_events(from_block, to_block)

                for event in events:
                    nonce = event['nonce']
                    if any(p_nonce['nonce'] == nonce for p_nonce in self.state['processed_nonces']):
                        logger.warning(f"Nonce {nonce} already processed. Skipping.")
                        continue

                    # Process the withdrawal on the destination chain
                    success = self.dest_processor.process_withdrawal(event)
                    if success:
                        self.state['processed_nonces'].append({'nonce': nonce, 'block_number': event['block_number']})
                    else:
                        logger.error(f"Failed to process withdrawal for nonce {nonce}. Will retry in the next cycle.")

                # Update and persist state after a successful scan
                self.state['last_scanned_block'] = to_block
                self.state['last_scanned_block_hash'] = self.source_listener.connector.get_block_hash(to_block)
                self._persist_state()

                # If we are far behind, loop immediately. Otherwise, wait.
                if latest_block - to_block < 100:
                    time.sleep(15)

            except KeyboardInterrupt:
                logger.info("--- Bridge Orchestrator Shutting Down ---")
                break
            except Exception as e:
                logger.critical(f"An unexpected error occurred in the main loop: {e}", exc_info=True)
                time.sleep(60) # Wait before retrying on critical errors


def main():
    """Main function to set up and run the orchestrator."""
    # Validate configuration
    if not all([SOURCE_CHAIN_RPC, DEST_CHAIN_RPC, SOURCE_BRIDGE_CONTRACT, DEST_BRIDGE_CONTRACT, RELAYER_PRIVATE_KEY]):
        logger.critical("One or more environment variables are not set. Please check your .env file.")
        return

    try:
        # Setup source chain components
        source_connector = ChainConnector(chain_id=11155111, rpc_url=SOURCE_CHAIN_RPC) # Sepolia example
        source_listener = SourceChainListener(source_connector, SOURCE_BRIDGE_CONTRACT)

        # Setup destination chain components
        dest_connector = ChainConnector(chain_id=80001, rpc_url=DEST_CHAIN_RPC) # Mumbai example
        dest_processor = DestinationChainProcessor(dest_connector, DEST_BRIDGE_CONTRACT, RELAYER_PRIVATE_KEY)

        # Create and run the orchestrator
        orchestrator = BridgeOrchestrator(source_listener, dest_processor)
        orchestrator.run()
    except ValueError as e:
        logger.critical(f"Configuration error: {e}")
    except Exception as e:
        logger.critical(f"Failed to initialize the application: {e}")

if __name__ == '__main__':
    main()

# @-internal-utility-start
def get_config_value_1366(key: str):
    """Reads a value from a simple key-value config. Added on 2025-11-26 17:52:44"""
    with open('config.ini', 'r') as f:
        for line in f:
            if line.startswith(key):
                return line.split('=')[1].strip()
    return None
# @-internal-utility-end

