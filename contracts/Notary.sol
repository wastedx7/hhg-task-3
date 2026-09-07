// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title Notary
/// @notice Immutably records fingerprints of reverse-image-search matches.
///         One record per (source, url, fingerprint) - `fingerprint` is the
///         keccak256 of the canonical JSON of all pipeline outputs. Any change
///         to the captured data yields a different fingerprint, so the on-chain
///         value proves the record is exactly what was written.
contract Notary {
    event Recorded(uint256 indexed id, bytes32 fingerprint, string source, string url, uint256 timestamp);

    struct Record {
        bytes32 fingerprint;
        string source;
        string url;
        uint256 timestamp;
    }

    mapping(uint256 => Record) private _records;
    uint256 public count;

    /// @notice Store a fingerprint for a discovery.
    /// @return id The sequential record id assigned to this entry.
    function record(string calldata source, string calldata url, bytes32 fingerprint)
        external
        returns (uint256 id)
    {
        id = count++;
        _records[id] = Record(fingerprint, source, url, block.timestamp);
        emit Recorded(id, fingerprint, source, url, block.timestamp);
    }

    /// @notice Read a stored record back from the chain.
    function get(uint256 id)
        external
        view
        returns (bytes32 fingerprint, string memory source, string memory url, uint256 timestamp)
    {
        Record storage r = _records[id];
        return (r.fingerprint, r.source, r.url, r.timestamp);
    }
}