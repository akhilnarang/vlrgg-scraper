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