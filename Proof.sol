// SPDX-License-Identifier: MIT
// © 2026 POG / UR24144 ProDiCeT, Université de Poitiers
pragma solidity ^0.8.19;

/// @title OmicChain Proof

contract Proof {
    event HashPublished(
        address indexed publisher,
        bytes32 indexed hash,
        uint256 timestamp
    );

    /// @notice Publish a SHA-256 fingerprint on-chain.
    /// @param hash The 32-byte SHA-256 digest to certify.
    function publishHash(bytes32 hash) external {
        emit HashPublished(msg.sender, hash, block.timestamp);
    }
}
