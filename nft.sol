// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/token/ERC721/extensions/ERC721URIStorage.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";


contract NFTProof is ERC721, ERC721URIStorage, Ownable, ReentrancyGuard {
    
    uint256 private _tokenIdCounter;
    
    mapping(uint256 => string) private _pipelineHashes;
    
    mapping(uint256 => string) private _proofTxHashes;
    
    event NFTMinted(
        address indexed recipient,
        uint256 indexed tokenId,
        string tokenURI,
        string pipelineHash,
        string proofTxHash
    );
    
    event MetadataUpdated(uint256 indexed tokenId, string newTokenURI);
    
    
    constructor() ERC721("NFTProof", "NFTP") Ownable(msg.sender) {
        _tokenIdCounter = 0;
    }
    
  
    function mintNFT(
        address recipient,
        string memory uri,
        string memory pipelineHash,
        string memory proofTxHash
    ) public onlyOwner nonReentrant returns (uint256) {
        require(recipient != address(0), "NFTProof: mint to zero address");
        require(bytes(uri).length > 0, "NFTProof: empty URI");
        require(bytes(pipelineHash).length > 0, "NFTProof: empty pipeline hash");
        
        uint256 tokenId = _tokenIdCounter;
        
        _safeMint(recipient, tokenId);
        
        _setTokenURI(tokenId, uri);
        
        _pipelineHashes[tokenId] = pipelineHash;
        _proofTxHashes[tokenId] = proofTxHash;
        
        unchecked {
            _tokenIdCounter++;
        }
        
        emit NFTMinted(recipient, tokenId, uri, pipelineHash, proofTxHash);
        
        return tokenId;
    }
    
   
    function getPipelineHash(uint256 tokenId) public view returns (string memory) {
        require(ownerOf(tokenId) != address(0), "NFTProof: token does not exist");
        return _pipelineHashes[tokenId];
    }
    
   
    function getProofTxHash(uint256 tokenId) public view returns (string memory) {
        require(ownerOf(tokenId) != address(0), "NFTProof: token does not exist");
        return _proofTxHashes[tokenId];
    }
    
   
    function getCertificationInfo(uint256 tokenId) 
        public 
        view 
        returns (
            string memory uri,
            string memory pipelineHash,
            string memory proofTxHash,
            address owner
        ) 
    {
        require(ownerOf(tokenId) != address(0), "NFTProof: token does not exist");
        
        return (
            tokenURI(tokenId),
            _pipelineHashes[tokenId],
            _proofTxHashes[tokenId],
            ownerOf(tokenId)
        );
    }
    
   
    function totalSupply() public view returns (uint256) {
        return _tokenIdCounter;
    }
    
  
    function updateTokenURI(uint256 tokenId, string memory newUri) 
        public 
        onlyOwner 
    {
        require(ownerOf(tokenId) != address(0), "NFTProof: token does not exist");
        require(bytes(newUri).length > 0, "NFTProof: empty URI");
        
        _setTokenURI(tokenId, newUri);
        emit MetadataUpdated(tokenId, newUri);
    }
    
   
    function tokenURI(uint256 tokenId)
        public
        view
        override(ERC721, ERC721URIStorage)
        returns (string memory)
    {
        return super.tokenURI(tokenId);
    }
    
   
    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(ERC721, ERC721URIStorage)
        returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }
}