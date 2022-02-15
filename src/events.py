import requests
from bs4 import BeautifulSoup
from requests.api import head

class Events:
    """
    Get events from the VLR events page
    """

    def events(self, region):
        URL = "https://www.vlr.gg/events/" + region
        page = requests.get(URL)

        soup = BeautifulSoup(page.content, 'html.parser')
        container1 = soup.find_all('div', class_="events-container-col")[0]
        container2 = soup.find_all('div', class_="events-container-col")[1]
        upcoming = self.eventsList(container1)
        completed = self.eventsList(container2)
        return { "upcoming" : upcoming, "completed": completed }

    def eventsList(self, div):
        events = []
        eventsHTML = div.find_all('a', class_="wf-card")
        for eventHTML in eventsHTML:
            id = eventHTML['href'].split('/')[2]
            title = eventHTML.find_all('div', class_="event-item-title")[0].get_text().strip()
            status = eventHTML.find_all('span', class_="event-item-desc-item-status")[0].get_text().strip()
            prize = eventHTML.find_all('div', class_="mod-prize")[0].get_text().strip().replace('\t', '').split('\n')[0]
            dates = eventHTML.find_all('div', class_="mod-dates")[0].get_text().strip().replace('\t', '').split('\n')[0]
            location = eventHTML.find_all('div',class_="mod-location")[0].find_all('i', class_="flag")[0].get('class')[1].replace('mod-', '')
            img = eventHTML.find_all('div', class_="event-item-thumb")[0].find('img')['src']
            if img == '/img/vlr/tmp/vlr.png':
                img = "https://vlr.gg" + img
            else:
                img = "https:" + img
            event = { 'id': id, 'title': title, 'status': status, 'prize': prize, 'dates': dates, 'location': location, 'img': img }
            events.append(event)
        return events



    def event(self,id):
        event = {}
        event['id'] = id
        URL = "https://www.vlr.gg/event/" + id
        page = requests.get(URL)
        soup = BeautifulSoup(page.content, 'html.parser')

        header = soup.find_all('div', class_="event-header")[0]
        event['title'] = header.find_all('h1', class_="wf-title")[0].get_text().strip()
        event['subtitle'] = header.find_all('h2', class_="event-desc-subtitle")[0].get_text().strip()
        event['dates'] = header.find_all('div',class_='event-desc-item-value')[0].get_text().strip()
        event['prize'] = header.find_all('div',class_='event-desc-item-value')[1].get_text().strip().replace('\t', '').replace('\n', ' ')
        event['location'] = header.find_all('div',class_='event-desc-item-value')[2].find_all('i', class_="flag")[0].get('class')[1].replace('mod-', '')
        img = header.find_all('div',class_='event-header-thumb')[0].find('img')['src']
        if img == '/img/vlr/tmp/vlr.png':
            img = "https://vlr.gg" + img
        else:
            img = "https:" + img
        event['img'] = img

        if len(soup.find_all('table', class_="wf-table")) > 0:
            prizesTable = soup.find_all('table', class_="wf-table")[-1]
            event['prizes'] = self.prizesParser(prizesTable)


        if len(soup.find_all('div',class_="event-brackets-container")) > 1:
            bracketContainers = soup.find_all('div',class_="event-brackets-container")
            brackets = []
            for container in bracketContainers:
                upperBracket = []
                if len(container.find_all('div', class_="bracket-container mod-upper")) > 0:
                    upperBracketContainer = container.find_all('div', class_="bracket-container mod-upper")[0]
                    uppercols = upperBracketContainer.find_all('div', class_="bracket-col")
                    for col in uppercols:
                        upperBracket.append(self.bracketParser(col))
                if len(container.find_all('div', class_="bracket-container mod-upper mod-compact")) > 0:
                    upperBracketContainer = container.find_all('div', class_="bracket-container mod-upper mod-compact")[0]
                    uppercols = upperBracketContainer.find_all('div', class_="bracket-col")
                    for col in uppercols:
                        upperBracket.append(self.bracketParser(col))

                lowerBracket = []
                if len(container.find_all('div', class_="bracket-container mod-lower")) > 0:
                    lowerBracketContainer = container.find_all('div', class_="bracket-container mod-lower")[0]
                    lowercols = lowerBracketContainer.find_all('div', class_="bracket-col")
                    for col in lowercols:
                        lowerBracket.append(self.bracketParser(col))
                if len(container.find_all('div', class_="bracket-container mod-lower")) == 0:
                    lowerBracketContainer = container.find_all('div', class_="bracket-container mod-lower mod-compact")[0]
                    lowercols = lowerBracketContainer.find_all('div', class_="bracket-col")
                    for col in lowercols:
                        lowerBracket.append(self.bracketParser(col))
                brackets.append({ 'upper' : upperBracket, 'lower': lowerBracket })
            event['bracket'] = brackets
        else:
            upperBracket = []
            if len(soup.find_all('div', class_="bracket-container mod-upper")) > 0:
                upperBracketContainer = soup.find_all('div', class_="bracket-container mod-upper")[0]
                uppercols = upperBracketContainer.find_all('div', class_="bracket-col")
                for col in uppercols:
                    upperBracket.append(self.bracketParser(col))
            if len(soup.find_all('div', class_="bracket-container mod-upper mod-compact")) > 0:
                upperBracketContainer = soup.find_all('div', class_="bracket-container mod-upper mod-compact")[0]
                uppercols = upperBracketContainer.find_all('div', class_="bracket-col")
                for col in uppercols:
                    upperBracket.append(self.bracketParser(col))

            lowerBracket = []
            if len(soup.find_all('div', class_="bracket-container mod-lower")) > 0:
                lowerBracketContainer = soup.find_all('div', class_="bracket-container mod-lower")[0]
                lowercols = lowerBracketContainer.find_all('div', class_="bracket-col")
                for col in lowercols:
                    lowerBracket.append(self.bracketParser(col))
            if len(soup.find_all('div', class_="bracket-container mod-lower mod-compact")) > 0:
                lowerBracketContainer = soup.find_all('div', class_="bracket-container mod-lower mod-compact")[0]
                lowercols = lowerBracketContainer.find_all('div', class_="bracket-col")
                for col in lowercols:
                    lowerBracket.append(self.bracketParser(col))
            event['bracket'] = [{ 'upper' : upperBracket, 'lower': lowerBracket }]

        participants = []
        if len(soup.find_all('div', class_="event-teams-container")) > 0:
            teamsContainer = soup.find_all('div', class_="event-teams-container")[0]
            teamItems = teamsContainer.find_all('div', class_="wf-card event-team")
            for team in teamItems:
                participant = {}
                roster = []
                participant['team'] = team.find_all('a', class_="event-team-name")[0].get_text().strip()
                participant['id'] = team.find_all('a', class_="event-team-name")[0]['href'].split('/')[2]
                img = team.find_all('img', class_="event-team-players-mask-team")[0]['src']
                if img == '/img/vlr/tmp/vlr.png':
                    img = "https://vlr.gg" + img
                else:
                    img = "https:" + img
                participant['img'] = img
                if len(team.find_all('div', class_="wf-module-item")) > 0:
                    participant['seed'] = team.find_all('div', class_="wf-module-item")[0].get_text().strip()
                else:
                    participant['seed'] = ""
                players = team.find_all('a', class_="event-team-players-item")
                for player in players:
                    playerID = player['href'].split('/')[2]
                    playerName = player.get_text().strip()
                    country = player.find_all('i', class_="flag")[0].get('class')[1].replace('mod-', '')
                    roster.append({'playerID': playerID, 'playerName': playerName, 'country': country})
                participant['roster'] = roster
                participants.append(participant)
            event['participants'] = participants



        URL = "https://www.vlr.gg/event/matches/" + id + "/?series_id=all"
        page = requests.get(URL)
        soup = BeautifulSoup(page.content, 'html.parser')
        matches = []
        dates = soup.find_all('div', class_="wf-label mod-large")
        for (day, date) in enumerate(dates):
            matchesOnDay = soup.find_all('div', class_="wf-card")[day + 1]
            matches.append({'date' : date.get_text().strip(), 'matches' : self.matchParser(matchesOnDay)})

        event['matches'] = matches

        return event


    def bracketParser(self, bracketCol):
        matches = []
        title = bracketCol.find_all('div',class_="bracket-col-label")[0].get_text().strip()
        matchesHTML = bracketCol.find_all('a',class_="bracket-item")
        for matchHTML in matchesHTML:
            match = {}
            if matchHTML.get('href') != None:
                match['id'] = matchHTML.get('href').split('/')[1]
            if matchHTML.find('div',class_="bracket-item-status") != None:
                match['time'] = matchHTML.find('div',class_="bracket-item-status").get_text().strip()
            teams = []
            for i in range(0,2):
                team = {}
                team['name'] = matchHTML.find_all('div',class_="bracket-item-team-name")[i].get_text().strip()
                img = matchHTML.find_all('div',class_="bracket-item-team-name")[i].find('img')['src']
                if img == '/img/vlr/tmp/vlr.png':
                    img = "https://vlr.gg" + img
                else:
                    img = "https:" + img
                team['img'] = img
                team['score'] = matchHTML.find_all('div',class_="bracket-item-team-score")[i].get_text().strip()
                teams.append(team)
            match['teams'] = teams
            matches.append(match)
        return { 'title': title, 'matches': matches }

    def prizesParser(self, prizesTable):
        prizes = []
        rows  = prizesTable.find('tbody').find_all('tr')[:3]
        for row in rows:
            prize = {}
            prize['position'] = row.find_all('td')[0].get_text().strip()
            prize['prize'] = row.find_all('td')[1].get_text().strip().replace('\t','')
            teamRow = row.find_all('td')[2]
            if len(teamRow.find_all('a')) > 0:
                team = {}
                team['name'] = teamRow.find_all('div',class_="standing-item-team-name")[0].get_text().strip().split('\n')[0].strip()
                team['id'] = teamRow.find_all('a')[0]['href'].split('/')[2]
                img = teamRow.find('img')['src']
                if img == '/img/vlr/tmp/vlr.png':
                    img = "https://vlr.gg" + img
                else:
                    img = "https:" + img
                team['img'] =  img
                team['country'] = teamRow.find_all('div',class_="ge-text-light")[0].get_text().strip()
                prize['team'] = team
            else:
                prize['team'] = "TBD"
            prizes.append(prize)
        return prizes

    def matchParser(self, matchesOnDay):
        matchesList = matchesOnDay.find_all('a', class_="match-item")
        matches = []
        for matchHTML in matchesList:
            match = {}
            match['id'] = matchHTML['href'].split('/')[1]
            match['time'] = matchHTML.find_all('div',class_="match-item-time")[0].get_text().strip()
            teamsHTML = matchHTML.find_all('div',class_="match-item-vs-team")
            teams = []
            for teamHTML in teamsHTML:
                team = {}
                team['name'] = teamHTML.find_all('div', class_="match-item-vs-team-name")[0].get_text().strip()
                team['region'] = teamHTML.find_all('span',class_="flag")[0].get('class')[1].replace('mod-', '')
                team['score'] = teamHTML.find_all('div',class_="match-item-vs-team-score")[0].get_text().strip()
                teams.append(team)
            match['teams'] = teams
            match['status'] = matchHTML.find_all('div', class_="ml-status")[0].get_text().strip()
            if (match['status'] != "LIVE") and (match['status'] != "TBD"):
                match['eta'] = matchHTML.find_all('div', class_="ml-eta")[0].get_text().strip()
            match['round'] = matchHTML.find_all('div', class_="match-item-event text-of")[0].get_text().strip().split('\n')[0].strip()
            match['stage'] = matchHTML.find_all('div', class_="match-item-event text-of")[0].get_text().strip().split('\n')[1].strip()
            matches.append(match)
        return matches