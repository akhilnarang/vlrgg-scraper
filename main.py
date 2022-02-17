from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware

from src.news import News
from src.matches import Matches
from src.team import Team
from src.player import Player
from src.events import Events
from src.streams import Streams
from src.rankings import Rankings

app = FastAPI(
    title="VLR.gg Scraper",
    description="Almost died while scraping this, but did it.",
    version="0.5",
)

app.add_middleware(GZipMiddleware)

@app.get("/", tags=["health"])
async def health_check():
    """
    Checks site health
    """
    return 'OK'

@app.get("/news", tags=["news"])
async def news():
    """
    Gets news from the VLR homepage
    """
    return News.news()

@app.get("/upcoming", tags=["news", "matches"])
async def matches():
    """
    Gets upcoming matches from the VLR homepage
    """
    return Matches.upcoming_matches()

@app.get("/results", tags=["news", "matches"])
async def matches():
    """
    Gets recent match results from the VLR homepage
    """
    return Matches.recent_matches()

@app.get("/matches/schedule", tags=["matches"])
async def matches():
    """
    Gets upcoming matches from the VLR matches
    """
    return Matches.match_schedule()

@app.get("/matches/results", tags=["matches"])
async def matches():
    """
    Gets results of recent matches from the VLR matches
    """
    return Matches.match_results()

@app.get("/streams", tags=["news"])
async def streams():
    """
    Gets current streams from the VLR homepage
    """
    return Streams.streams()

@app.get("/match/{id}", tags=["matches"])
async def match(id):
    """
    Gets match details of the match with the given ID
    """
    return Matches.match(id)

@app.get("/team/{id}", tags=["teams"])
async def team(id):
    """
    Gets team details of the team with the given ID
    """
    return Team.team(id)

@app.get("/player/{id}", tags=["players"])
async def player(id):
    """
    Gets player details of the player with the given ID
    """
    return Player.player(id)

@app.get("/events", tags=["events"])
async def events():
    """
    Gets all events from the VLR events page
    """
    events = Events()
    return events.events("")

@app.get("/events/{region}", tags=["events"])
async def events(region):
    """
    Gets all events in a particular region
    """
    events = Events()
    return events.events(region)

@app.get("/event/{id}", tags=["events"])
async def events(id):
    """
    Gets event details of the event with the given ID
    """
    events = Events()
    return events.event(id)

@app.get("/rankings", tags=["rankings"])
async def rankings():
    """
    Gets world rankings of the all the teams, region wise
    """
    rankings = Rankings()
    return rankings.worldRanking()

@app.get("/rankings/regions", tags=["rankings"])
async def rankings():
    """
    Gets the regions for the rankings table
    """
    rankings = Rankings()
    return rankings.regions()

@app.get("/rankings/{region}", tags=["rankings"])
async def rankings(region):
    """
    Gets regional rankings of the all the teams in a region
    """
    rankings = Rankings()
    return rankings.regionRankings(region)
