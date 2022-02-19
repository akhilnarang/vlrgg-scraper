def match(id):
    """
    Details of a particular match from VLR
    """

    URL = 'https://www.vlr.gg/' + id
    page = requests.get(URL)

    soup = BeautifulSoup(page.content, 'html.parser')
    matchHeader = soup.find_all("div", class_="match-header-vs")[0]
    team1name = matchHeader.find_all("div", class_="wf-title-med")[0].get_text().strip()
    team1img = matchHeader.find_all("a", class_="match-header-link")[0].find('img')['src']
    if team1img == '/img/vlr/tmp/vlr.png':
        team1img = "https://vlr.gg" + team1img
    else:
        team1img = "https:" + team1img
    team2name = matchHeader.find_all("div", class_="wf-title-med")[1].get_text().strip()
    team2img = matchHeader.find_all("a", class_="match-header-link")[1].find('img')['src']
    print(team2img)
    if team2img == '/img/vlr/tmp/vlr.png':
        team2img = "https://vlr.gg" + team2img
    else:
        team2img = "https:" + team2img
    score = matchHeader.find_all("div", class_="match-header-vs-score")[0].find_all("div", class_="js-spoiler")[
        0].get_text().replace('\n', '').replace('\t', '')
    if len(soup.find_all("div", class_="match-header-note")) > 0:
        note = soup.find_all("div", class_="match-header-note")[0].get_text().strip()
    else:
        note = ""

    event = {}
    eventLink = soup.find_all("a", class_="match-header-event")[0]
    event['id'] = eventLink['href'].split('/')[2]
    img = eventLink.find('img')['src']
    if img == '/img/vlr/tmp/vlr.png':
        img = "https://vlr.gg" + img
    else:
        img = "https:" + img
    event['img'] = img
    event['series'] = eventLink.find_all('div')[0].find_all('div')[0].get_text().strip()
    event['stage'] = eventLink.find_all('div', class_="match-header-event-series")[0].get_text().strip().replace('\t',
                                                                                                                 '').replace(
        '\n', '')
    event['date'] = \
    soup.find_all('div', class_="match-header-date")[0].get_text().strip().replace('\t', '').replace('\n', ' ').replace(
        '    ', ', ').split('   ')[0]
    team1 = {'name': team1name, 'img': team1img}
    team2 = {'name': team2name, 'img': team2img}
    teams = [team1, team2]
    stats = soup.find_all("div", class_="vm-stats")[0]
    maps = []
    for map in stats.find_all("div", class_="vm-stats-gamesnav-item"):
        name = map.get_text().strip().replace('\n', '').replace('\t', '')
        name = ''.join(i for i in name if not i.isdigit())
        id = map['data-game-id']
        maps.append({'name': name, 'id': id})

    mapStats = stats.find_all("div", class_="vm-stats-game")
    mapName = ''
    team1Obj = {}
    team2Obj = {}
    mapData = []
    for map in mapStats:
        id = map['data-game-id']
        if id != 'all':
            score1 = map.find_all("div", class_="score")[0].get_text().strip()
            team1 = map.find_all("div", class_="team-name")[0].get_text().strip()
            score2 = map.find_all("div", class_="score")[1].get_text().strip()
            team2 = map.find_all("div", class_="team-name")[1].get_text().strip()
            print([map1['name'] for map1 in maps if map1['id'] == id][0])
            print(team1, score1)
            print(team2, score2)
            mapName = [map1['name'] for map1 in maps if map1['id'] == id][0]
            team1Obj = {'name': team1, 'score': score1}
            team2Obj = {'name': team2, 'score': score2}
            print('')
        else:
            print([map1['name'] for map1 in maps if map1['id'] == id][0])
            mapName = [map1['name'] for map1 in maps if map1['id'] == id][0]
            team1Obj = {}
            team2Obj = {}
            print('')
        scoreboard = map.find_all('tbody')
        members = []

        maprounds = map.find_all("div", class_="vlr-rounds-row-col")[1:]
        id = map['data-game-id']
        rounds = []
        if id != 'all':
            prev = [0, 0]
            for round in maprounds:
                current = []
                roundWinner = ""
                if len(round.find_all("div", class_="rnd-currscore")) > 0:
                    roundNum = round.find_all("div", class_="rnd-num")[0].get_text().strip()
                    roundScore = round.find_all("div", class_="rnd-currscore")[0].get_text().strip()
                    print(roundScore)
                    if roundScore != "":
                        current = [int(i) for i in roundScore.split("-")]
                        if prev[0] == current[0]:
                            roundWinner = "team2"
                        elif prev[1] == current[1]:
                            roundWinner = "team1"
                        prev = current
                    if len(round.find_all("div", class_="mod-win")) > 0:
                        raw = round.find_all("div", class_="mod-win")[0].find('img')['src']
                        if 'mod-t' in round.find_all("div", class_="mod-win")[0].get('class'):
                            side = "attack"
                        elif 'mod-ct' in round.find_all("div", class_="mod-win")[0].get('class'):
                            side = "defense"
                        winType = ''
                        if 'elim' in raw:
                            winType = 'Elimination'
                        elif 'time' in raw:
                            winType = 'Time out'
                        elif 'defuse' in raw:
                            winType = 'Defused'
                        elif 'boom' in raw:
                            winType = 'Spiked out'
                        else:
                            winType = 'Not Played'
                    else:
                        winType = 'Not Played'
                    print(roundNum, roundScore, winType)
                rounds.append({'roundNum': roundNum, 'roundScore': roundScore, 'winner': roundWinner, 'side': side,
                               'winType': winType})

        for row in scoreboard:
            for team in row.find_all('tr'):
                name = team.find_all('td', class_='mod-player')[0].find_all('div', class_='text-of')[
                    0].get_text().strip()
                teamName = team.find_all('td', class_='mod-player')[0].find_all('div', class_='ge-text-light')[
                    0].get_text().strip()
                agents = []
                ACS = team.find_all('td', class_='mod-stat')[0].find_all('span', class_='stats-sq')[
                    0].get_text().strip()
                kills = team.find_all('td', class_='mod-vlr-kills')[0].find_all('span', class_='stats-sq')[
                    0].get_text().strip()
                deaths = team.find_all('td', class_='mod-vlr-deaths')[0].find_all('span', class_='stats-sq')[
                    0].get_text().strip().replace('/', '')
                assists = team.find_all('td', class_='mod-vlr-assists')[0].find_all('span', class_='stats-sq')[
                    0].get_text().strip()
                hs = team.find_all('td', class_='mod-stat')[6].find_all('span', class_='stats-sq')[0].get_text().strip()
                agentHTML = team.find_all('td', class_='mod-agents')[0].find_all('img')
                for agent in agentHTML:
                    title = agent['title']
                    src = agent['src']
                    acs = ACS
                    agents.append({'name': title, 'img': "https://vlr.gg" + src})
                member = {'name': name, 'team': teamName, 'agents': agents, 'acs': acs, 'kills': kills,
                          'deaths': deaths, 'assists': assists, 'HSpercent': hs}
                members.append(member)
                print(name, teamName, agents)
            print('')
        mapData.append({'map': mapName, 'teams': [team1Obj, team2Obj], 'members': members, 'rounds': rounds})

    if len(soup.find_all('div', class_="match-h2h-matches")) > 0:
        head2headContainer = soup.find_all('div', class_="match-h2h-matches")[0]
        h2hMatchLinks = head2headContainer.find_all('a', class_="wf-module-item mod-h2h")
        h2hMatches = []
        for matchLink in h2hMatchLinks:
            h2h = {}
            h2h['id'] = matchLink['href'].split('/')[1]
            h2hEvent = matchLink.find_all('div', class_="match-h2h-matches-event-name text-of")[0].get_text().strip()
            h2hStage = matchLink.find_all('div', class_="match-h2h-matches-event-series text-of")[0].get_text().strip()
            img = matchLink.find_all('div', class_="match-h2h-matches-event")[0].find('img')['src']
            if img == '/img/vlr/tmp/vlr.png':
                img = "https://vlr.gg" + img
            else:
                img = "https:" + img
            h2h['event'] = {'img': img, 'series': h2hEvent, 'stage': h2hStage}
            h2h['event']['date'] = matchLink.find_all('div', class_="match-h2h-matches-date")[0].get_text().strip()
            images = matchLink.find_all('img', class_="match-h2h-matches-team")
            score1 = matchLink.find_all('span', class_="rf")[0]
            score2 = matchLink.find_all('span', class_="ra")[0]
            h2h['teams'] = []
            for image in images:
                img = image['src']
                if img == '/img/vlr/tmp/vlr.png':
                    img = "https://vlr.gg" + img
                else:
                    img = "https:" + img
                isWin = False
                if 'mod-win' in image.get('class'):
                    isWin = True
                h2h['teams'].append({'team': img, 'winner': isWin})
            h2h['teams'][0]['score'] = score1.get_text().strip()
            h2h['teams'][1]['score'] = score2.get_text().strip()
            h2hMatches.append(h2h)
    else:
        h2hMatches = []
    return {'teams': teams, 'score': score, 'note': note, 'event': event, 'data': mapData, 'head2head': h2hMatches}