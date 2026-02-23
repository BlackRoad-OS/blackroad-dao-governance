"""
DAO (Decentralized Autonomous Organization) Governance System
Manages proposals, voting, and member participation with SQLite persistence.
"""

import sqlite3
import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Literal
import json

# Database location
DB_PATH = Path.home() / ".blackroad" / "dao.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@dataclass
class Proposal:
    id: str
    title: str
    description: str
    proposer: str
    votes_for: int = 0
    votes_against: int = 0
    votes_abstain: int = 0
    status: str = "draft"  # draft, active, passed, rejected, executed, expired
    quorum_pct: float = 51.0
    deadline_ts: float = 0.0
    created_at: float = 0.0
    executed_at: Optional[float] = None


@dataclass
class Member:
    address: str
    voting_power: int = 1
    joined_at: float = 0.0
    proposals_created: int = 0
    votes_cast: int = 0


class DAOGovernance:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH))
        self.cursor = self.conn.cursor()
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS members (
                address TEXT PRIMARY KEY,
                voting_power INTEGER DEFAULT 1,
                joined_at REAL,
                proposals_created INTEGER DEFAULT 0,
                votes_cast INTEGER DEFAULT 0
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                proposer TEXT,
                votes_for INTEGER DEFAULT 0,
                votes_against INTEGER DEFAULT 0,
                votes_abstain INTEGER DEFAULT 0,
                status TEXT DEFAULT 'draft',
                quorum_pct REAL DEFAULT 51.0,
                deadline_ts REAL,
                created_at REAL,
                executed_at REAL
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                proposal_id TEXT,
                voter_address TEXT,
                vote TEXT,
                weight INTEGER,
                voted_at REAL,
                PRIMARY KEY (proposal_id, voter_address)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS treasury (
                asset_id TEXT PRIMARY KEY,
                amount REAL,
                symbol TEXT,
                updated_at REAL
            )
        """)
        self.conn.commit()

    def register_member(self, address: str, voting_power: int = 1) -> Member:
        """Register a new member with voting power."""
        now = datetime.now().timestamp()
        try:
            self.cursor.execute(
                "INSERT INTO members (address, voting_power, joined_at) VALUES (?, ?, ?)",
                (address, voting_power, now)
            )
            self.conn.commit()
            return Member(address, voting_power, now, 0, 0)
        except sqlite3.IntegrityError:
            return self.get_member(address)

    def get_member(self, address: str) -> Optional[Member]:
        """Retrieve member details."""
        self.cursor.execute("SELECT * FROM members WHERE address = ?", (address,))
        row = self.cursor.fetchone()
        if row:
            return Member(*row)
        return None

    def create_proposal(
        self, title: str, description: str, proposer: str, 
        voting_period_days: int = 7, quorum_pct: float = 51.0
    ) -> Proposal:
        """Create a new proposal."""
        now = datetime.now().timestamp()
        deadline = now + timedelta(days=voting_period_days).total_seconds()
        proposal_id = f"prop_{int(now * 1000)}"
        
        self.cursor.execute(
            """INSERT INTO proposals 
               (id, title, description, proposer, status, quorum_pct, deadline_ts, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (proposal_id, title, description, proposer, "active", quorum_pct, deadline, now)
        )
        
        # Increment proposer's count
        self.cursor.execute(
            "UPDATE members SET proposals_created = proposals_created + 1 WHERE address = ?",
            (proposer,)
        )
        self.conn.commit()
        
        return Proposal(proposal_id, title, description, proposer, 0, 0, 0, "active", quorum_pct, deadline, now)

    def vote(self, proposal_id: str, voter_address: str, vote: Literal["for", "against", "abstain"]):
        """Cast a weighted vote on a proposal."""
        member = self.get_member(voter_address)
        if not member:
            raise ValueError(f"Member {voter_address} not registered")
        
        # Check if already voted
        self.cursor.execute(
            "SELECT vote FROM votes WHERE proposal_id = ? AND voter_address = ?",
            (proposal_id, voter_address)
        )
        if self.cursor.fetchone():
            raise ValueError(f"Member {voter_address} already voted on {proposal_id}")
        
        now = datetime.now().timestamp()
        weight = member.voting_power
        
        self.cursor.execute(
            """INSERT INTO votes (proposal_id, voter_address, vote, weight, voted_at)
               VALUES (?, ?, ?, ?, ?)""",
            (proposal_id, voter_address, vote, weight, now)
        )
        
        # Update vote counts in proposal
        if vote == "for":
            col = "votes_for"
        elif vote == "against":
            col = "votes_against"
        else:
            col = "votes_abstain"
        
        self.cursor.execute(
            f"UPDATE proposals SET {col} = {col} + ? WHERE id = ?",
            (weight, proposal_id)
        )
        
        # Increment votes_cast for member
        self.cursor.execute(
            "UPDATE members SET votes_cast = votes_cast + 1 WHERE address = ?",
            (voter_address,)
        )
        self.conn.commit()

    def tally_votes(self, proposal_id: str) -> Dict:
        """Tally votes and determine result."""
        self.cursor.execute(
            "SELECT votes_for, votes_against, votes_abstain, quorum_pct FROM proposals WHERE id = ?",
            (proposal_id,)
        )
        row = self.cursor.fetchone()
        if not row:
            raise ValueError(f"Proposal {proposal_id} not found")
        
        votes_for, votes_against, votes_abstain, quorum_pct = row
        total_votes = votes_for + votes_against + votes_abstain
        
        # Get total voting power for quorum calculation
        self.cursor.execute("SELECT SUM(voting_power) FROM members")
        total_power = self.cursor.fetchone()[0] or 1
        
        participation_pct = (total_votes / total_power * 100) if total_power > 0 else 0
        quorum_met = participation_pct >= quorum_pct
        
        if total_votes == 0:
            return {
                "for_pct": 0, "against_pct": 0, "abstain_pct": 0,
                "quorum_met": False, "result": "no votes"
            }
        
        for_pct = votes_for / total_votes * 100
        against_pct = votes_against / total_votes * 100
        abstain_pct = votes_abstain / total_votes * 100
        
        result = "passed" if quorum_met and for_pct > 50 else "rejected"
        
        return {
            "for_pct": round(for_pct, 2),
            "against_pct": round(against_pct, 2),
            "abstain_pct": round(abstain_pct, 2),
            "quorum_met": quorum_met,
            "result": result
        }

    def execute_proposal(self, proposal_id: str) -> bool:
        """Execute a passed proposal."""
        self.cursor.execute(
            "SELECT status FROM proposals WHERE id = ?",
            (proposal_id,)
        )
        row = self.cursor.fetchone()
        if not row:
            raise ValueError(f"Proposal {proposal_id} not found")
        
        tally = self.tally_votes(proposal_id)
        if tally["result"] != "passed":
            return False
        
        now = datetime.now().timestamp()
        self.cursor.execute(
            "UPDATE proposals SET status = 'executed', executed_at = ? WHERE id = ?",
            (now, proposal_id)
        )
        self.conn.commit()
        return True

    def get_active_proposals(self) -> List[Proposal]:
        """Get all proposals still in voting period."""
        now = datetime.now().timestamp()
        self.cursor.execute(
            "SELECT * FROM proposals WHERE status = 'active' AND deadline_ts > ?",
            (now,)
        )
        proposals = []
        for row in self.cursor.fetchall():
            proposals.append(Proposal(*row))
        return proposals

    def get_member_stats(self, address: str) -> Dict:
        """Get member participation statistics."""
        member = self.get_member(address)
        if not member:
            return {}
        
        # Count proposals won and lost
        self.cursor.execute(
            "SELECT id FROM proposals WHERE proposer = ?",
            (address,)
        )
        proposals_created = len(self.cursor.fetchall())
        
        votes_cast = member.votes_cast
        total_members = self._get_total_members()
        participation_rate = (votes_cast / (proposals_created or 1)) if proposals_created else 0
        
        return {
            "address": address,
            "voting_power": member.voting_power,
            "proposals_created": member.proposals_created,
            "votes_cast": member.votes_cast,
            "participation_rate": round(participation_rate, 3),
            "joined_at": datetime.fromtimestamp(member.joined_at).isoformat()
        }

    def _get_total_members(self) -> int:
        """Get total number of members."""
        self.cursor.execute("SELECT COUNT(*) FROM members")
        return self.cursor.fetchone()[0]

    def treasury_balance(self) -> Dict:
        """Get mock treasury balance with asset breakdown."""
        self.cursor.execute("SELECT asset_id, amount, symbol FROM treasury")
        rows = self.cursor.fetchall()
        if not rows:
            # Initialize with mock data
            mock_assets = [
                ("eth", 100.5, "ETH"),
                ("usdc", 50000.0, "USDC"),
                ("dao_token", 1000000.0, "DAO")
            ]
            for asset_id, amount, symbol in mock_assets:
                self.cursor.execute(
                    "INSERT INTO treasury (asset_id, amount, symbol, updated_at) VALUES (?, ?, ?, ?)",
                    (asset_id, amount, symbol, datetime.now().timestamp())
                )
            self.conn.commit()
            rows = mock_assets
        
        return {
            "total_assets": len(rows),
            "assets": [{"id": r[0], "amount": r[1], "symbol": r[2]} for r in rows]
        }

    def close(self):
        """Close database connection."""
        self.conn.close()


def main():
    parser = argparse.ArgumentParser(description="DAO Governance System")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Proposals command
    proposals_parser = subparsers.add_parser("proposals", help="List active proposals")
    
    # Vote command
    vote_parser = subparsers.add_parser("vote", help="Cast a vote")
    vote_parser.add_argument("proposal_id", help="Proposal ID")
    vote_parser.add_argument("voter", help="Voter address")
    vote_parser.add_argument("vote", choices=["for", "against", "abstain"], help="Vote choice")
    
    # Tally command
    tally_parser = subparsers.add_parser("tally", help="Tally votes for a proposal")
    tally_parser.add_argument("proposal_id", help="Proposal ID")
    
    args = parser.parse_args()
    
    dao = DAOGovernance()
    
    try:
        if args.command == "proposals":
            proposals = dao.get_active_proposals()
            print(f"Active Proposals: {len(proposals)}")
            for prop in proposals:
                print(f"  {prop.id}: {prop.title} (Status: {prop.status})")
        
        elif args.command == "vote":
            dao.vote(args.proposal_id, args.voter, args.vote)
            print(f"✓ Vote recorded: {args.voter} voted {args.vote} on {args.proposal_id}")
        
        elif args.command == "tally":
            tally = dao.tally_votes(args.proposal_id)
            print(f"Proposal {args.proposal_id} Tally:")
            print(f"  For: {tally['for_pct']}%")
            print(f"  Against: {tally['against_pct']}%")
            print(f"  Abstain: {tally['abstain_pct']}%")
            print(f"  Quorum Met: {tally['quorum_met']}")
            print(f"  Result: {tally['result']}")
    
    finally:
        dao.close()


if __name__ == "__main__":
    main()
