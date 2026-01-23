// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

contract HashPublisher {
    event HashPublished(address indexed publisher, string hash);

    function publishHash(string calldata hash) external {
        emit HashPublished(msg.sender, hash);
    }
}
