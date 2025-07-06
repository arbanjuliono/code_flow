# Code Flow - Cross-Chain Bridge Event Listener

This repository contains a Python-based simulation of a critical component for a cross-chain bridge: an event listener and transaction relayer. This script is designed to monitor a bridge contract on a source blockchain for deposit events and then initiate corresponding withdrawal transactions on a destination blockchain.

## Concept

A cross-chain bridge allows users to move assets or data from one blockchain (e.g., Ethereum) to another (e.g., Polygon). A common architecture for this involves a 'lock-and-mint' or 'burn-and-release' mechanism.

This script simulates the 'oracle' or 'relayer' part of this system. Its primary responsibilities are:
1.  **Listen**: Continuously scan a source blockchain for specific events (e.g., `TokensDeposited`) emitted by a smart contract.
2.  **Validate**: Ensure that each event is valid and has not been processed before to prevent double-spending.
3.  **Relay**: Construct, sign, and broadcast a transaction on the destination chain to complete the cross-chain action (e.g., call a `withdrawTokens` function).

This process must be robust, fault-tolerant, and resilient to blockchain-specific issues like network latency and chain re-organizations (re-orgs).

## Code Architecture

The script is designed with a modular, object-oriented approach to separate concerns and enhance maintainability. The core components are:

-   `ChainConnector`: A generic utility class responsible for establishing and managing a connection to a blockchain node via its RPC URL using the `web3.py` library. It provides basic methods like `is_connected()` and `get_latest_block_number()`.

-   `SourceChainListener`: This class is responsible for all interactions with the source chain. It uses a `ChainConnector` instance to connect to the source chain and is configured with the bridge contract's address. Its main function, `scan_for_events()`, polls a range of blocks for `TokensDeposited` events and parses them into a structured format.

-   `DestinationChainProcessor`: This class handles the logic for the destination chain. It takes the processed event data from the listener and simulates the process of creating, signing, and sending a withdrawal transaction. It manages the relayer's wallet and interacts with the destination bridge contract.

-   `BridgeOrchestrator`: This is the main controller class that coordinates the entire process. It contains the main application loop and manages the overall state. Its key responsibilities include:
    -   Initializing the listener and processor.
    -   Managing state persistence (last scanned block, processed event nonces) by saving to a local `bridge_state.json` file.
    -   Determining which blocks to scan.
    -   Handling chain re-organizations by checking block hashes against its saved state.
    -   Passing new, valid events to the `DestinationChainProcessor`.
    -   Implementing graceful shutdown and error handling.

## How it Works

The script operates in a continuous loop, performing the following steps:

1.  **Initialization**: The `BridgeOrchestrator` starts up, creating instances of the listener and processor. It loads its previous state from `bridge_state.json` or creates a default state if the file doesn't exist.

2.  **Health Check**: It verifies that the RPC connections to both the source and destination chains are active.

3.  **Re-org Check**: Before scanning, it checks for a potential chain re-organization on the source chain. It fetches the hash of the `last_scanned_block` from its state and compares it to the hash of the same block number on the live chain. If they don't match, it assumes a re-org occurred, rolls back its state by a safe number of blocks (`REORG_CONFIRMATION_DEPTH`), and prunes any processed nonces from the re-orged blocks.

4.  **Block Range Calculation**: It determines a new range of blocks to scan, starting from `last_scanned_block + 1` up to the latest block on the chain, minus the `REORG_CONFIRMATION_DEPTH`. This buffer ensures that events are only processed from blocks that are unlikely to be reverted.

5.  **Event Scanning**: It calls the `SourceChainListener` to query the calculated block range for `TokensDeposited` events.

6.  **Event Processing**: For each event found:
    -   It checks the event's `nonce` against the list of `processed_nonces` in its state.
    -   If the nonce is new, it passes the event data to the `DestinationChainProcessor`.
    -   The processor simulates building, signing, and sending the corresponding withdrawal transaction.
    -   If the simulated transaction is successful, the orchestrator adds the event's nonce to its list of processed nonces.

7.  **State Persistence**: After scanning the block range, the orchestrator updates its state (`last_scanned_block`, `last_scanned_block_hash`, `processed_nonces`) and saves it to `bridge_state.json`.

8.  **Wait**: The orchestrator waits for a short period (e.g., 15 seconds) before starting the next cycle to avoid spamming the RPC endpoints.

## Usage Example

Follow these steps to set up and run the bridge listener simulation.

### 1. Prerequisites

-   Python 3.8+
-   Access to RPC endpoints for two EVM-compatible chains (e.g., from Infura, Alchemy, or a local node). For this example, we'll use Sepolia (source) and Mumbai (destination).

### 2. Setup

Clone the repository:
```bash
git clone https://github.com/your-username/code_flow.git
cd code_flow
```

Create and activate a Python virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
# On Windows, use: venv\Scripts\activate
```

Install the required dependencies:
```bash
pip install -r requirements.txt
```

### 3. Configuration

Create a `.env` file in the root directory of the project. This file will store your sensitive configuration details. Populate it with the following variables:

```env
# RPC URL for the source chain (e.g., Ethereum Sepolia)
SOURCE_CHAIN_RPC="https://sepolia.infura.io/v3/YOUR_INFURA_PROJECT_ID"

# RPC URL for the destination chain (e.g., Polygon Mumbai)
DEST_CHAIN_RPC="https://polygon-mumbai.infura.io/v3/YOUR_INFURA_PROJECT_ID"

# Deployed bridge contract address on the source chain
SOURCE_BRIDGE_CONTRACT="0x..."

# Deployed bridge contract address on the destination chain
DEST_BRIDGE_CONTRACT="0x..."

# Private key of the relayer wallet (DO NOT USE A REAL WALLET WITH FUNDS FOR THIS SIMULATION)
RELAYER_PRIVATE_KEY="0x..."
```

**Note**: Replace the placeholder values with your actual RPC URLs and contract addresses. For the `RELAYER_PRIVATE_KEY`, use a key from a test wallet with no real value.

### 4. Running the Script

Execute the main script from your terminal:
```bash
python script.py
```

The script will start running. You will see log output in your console showing its progress:

```
2023-10-27 14:30:00 - INFO - [script] - --- Bridge Orchestrator Starting Up ---
2023-10-27 14:30:01 - INFO - [script] - Loaded state from bridge_state.json: {'last_scanned_block': 4500000, ...}
2023-10-27 14:30:16 - INFO - [script] - [Source Chain] Scanning for events from block 4500001 to 4500101.
2023-10-27 14:30:18 - INFO - [script] - [Source Chain] Found 1 new events.
2023-10-27 14:30:18 - INFO - [script] - [Dest Chain] Processing withdrawal for nonce 123 to 0xRecipientAddress...
2023-10-27 14:30:19 - INFO - [script] - [Dest Chain] SIMULATED sending withdrawal tx. Hash: 0x...
2023-10-27 14:30:20 - INFO - [script] - [Dest Chain] SIMULATED withdrawal for nonce 123 successful.
2023-10-27 14:30:35 - INFO - [script] - Waiting for new blocks to be confirmed... Current head: 4500110
```

To stop the script, press `Ctrl+C`.
