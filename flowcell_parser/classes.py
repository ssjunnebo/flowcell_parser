import re
import os
import csv
import xml.etree.ElementTree as ET
import logging
import glob
import json
from datetime import datetime

from collections import OrderedDict
from bs4 import BeautifulSoup  # html parser
from io import open


class RunParser(object):
    """Parses an Illumina run folder. It generates data for statusdb
    notable attributes :

    :RunInfoParser runinfo: see RunInfo
    :RunParametersParser runparameters: see RunParametersParser
    :SampleSheetParser samplesheet: see SampleSheetParser
    :LaneBarcodeParser lanebarcodes: see LaneBarcodeParser
    """
    def __init__(self, path):
        if os.path.exists(path):
            self.log = logging.getLogger(__name__)
            self.path = path
            self.parse()
            self.create_db_obj()
        else:
            raise os.error(' flowcell cannot be found at {}'.format(path))

    def parse(self, demultiplexingDir='Demultiplexing'):
        """Tries to parse as many files as possible from a run folder."""
        pattern = r'(\d{6})_([ST-]*\w+\d+)_\d+_([AB]?)([A-Z0-9\-]+)'
        m = re.match(pattern, os.path.basename(os.path.abspath(self.path)))
        fc_name = m.group(4)
        rinfo_path = os.path.join(self.path, 'RunInfo.xml')
        rpar_path = os.path.join(self.path, 'runParameters.xml')
        ss_path = os.path.join(self.path, 'SampleSheet.csv')
        cycle_times_log = os.path.join(self.path, 'Logs', "CycleTimes.txt")

        # These three are generate post-demultiplexing and could thus
        # potentially be replaced by reading from stats.json
        lb_path = os.path.join(self.path,
                               demultiplexingDir,
                               'Reports',
                               'html',
                               fc_name,
                               'all', 'all', 'all',
                               'laneBarcode.html')
        ln_path = os.path.join(self.path,
                               demultiplexingDir,
                               'Reports',
                               'html',
                               fc_name,
                               'all', 'all', 'all',
                               'lane.html')
        undeterminedStatsFolder = os.path.join(self.path,
                                               demultiplexingDir,
                                               'Stats')
        json_path = os.path.join(self.path,
                                 demultiplexingDir,
                                 'Stats',
                                 'Stats.json')

        try:
            self.runinfo = RunInfoParser(rinfo_path)
        except OSError as e:
            self.log.info(str(e))
            self.runinfo = None
        try:
            self.runparameters = RunParametersParser(rpar_path)
        except OSError as e:
            self.log.info(str(e))
            self.runparameters = None
        try:
            self.samplesheet = SampleSheetParser(ss_path)
        except OSError as e:
            self.log.info(str(e))
            self.samplesheet = None
        try:
            self.lanebarcodes = LaneBarcodeParser(lb_path)
        except OSError as e:
            self.log.info(str(e))
            self.lanebarcodes = None
        try:
            self.lanes = LaneBarcodeParser(ln_path)
        except OSError as e:
            self.log.info(str(e))
            self.lanes = None
        try:
            self.undet = DemuxSummaryParser(undeterminedStatsFolder)
        except OSError as e:
            self.log.info(str(e))
            self.undet = None
        try:
            self.time_cycles = CycleTimesParser(cycle_times_log)
        except OSError as e:
            self.log.info(str(e))
            self.time_cycles = None
        try:
            self.json_stats = StatsParser(json_path)
        except OSError as e:
            self.log.info(str(e))
            self.json_stats = None

    def create_db_obj(self):
        self.obj = {}
        bits = os.path.basename(os.path.abspath(self.path)).split('_')
        name = '{}_{}'.format(bits[0], bits[-1])
        self.obj['name'] = name
        if self.runinfo:
            self.obj['RunInfo'] = self.runinfo.data
            if self.runinfo.recipe:
                self.obj['run_setup'] = self.runinfo.recipe
        if self.runparameters:
            self.obj.update(self.runparameters.data)
            if self.runparameters.recipe:
                self.obj['run_setup'] = self.runparameters.recipe
        if self.samplesheet:
            self.obj['samplesheet_csv'] = self.samplesheet.data
        if self.lanebarcodes:
            self.obj['illumina'] = {}
            self.obj['illumina']['Demultiplex_Stats'] = {}
            self.obj['illumina']['Demultiplex_Stats']['Barcode_lane_statistics'] = \
                self.lanebarcodes.sample_data
            self.obj['illumina']['Demultiplex_Stats']['Flowcell_stats'] = \
                self.lanebarcodes.flowcell_data
            if self.lanes:
                self.obj['illumina']['Demultiplex_Stats']['Lanes_stats'] = \
                    self.lanes.sample_data
        if self.undet:
            self.obj['Undetermined'] = self.undet.result
        if self.time_cycles:
            for cycle in self.time_cycles.cycles:
                for k, v in cycle.items():
                    cycle[k] = str(v)
            self.obj['time cycles'] = self.time_cycles.cycles
        if self.json_stats:
            self.obj['Json_Stats'] = self.json_stats.data


class DemuxSummaryParser(object):
    def __init__(self, path):
        if os.path.exists(path):
            self.path = path
            self.result = {}
            self.TOTAL = {}
            self.parse()
        else:
            raise os.error('DemuxSummary folder {} cannot be found'.format(path))

    def parse(self):
        # Will only save the 50 more frequent indexes
        pattern = re.compile('DemuxSummaryF1L([0-9]).txt')
        for file in glob.glob(os.path.join(self.path, 'DemuxSummaryF1L?.txt')):
            lane_nb = pattern.search(file).group(1)
            self.result[lane_nb] = OrderedDict()
            self.TOTAL[lane_nb] = 0
            with open(file, newline='') as f:
                undeterminePart = False
                for line in f:
                    if not undeterminePart:
                        if '### Columns:' in line:
                            undeterminePart = True
                    else:
                        # It means I am readng the index_Sequence  Hit_Count
                        components = line.rstrip().split('\t')
                        if len(self.result[lane_nb].keys()) < 50:
                            self.result[lane_nb][components[0]] = int(components[1])
                        self.TOTAL[lane_nb] += int(components[1])


class LaneBarcodeParser(object):
    def __init__(self, path):
        if os.path.exists(path):
            self.path = path
            self.parse()
        else:
            raise os.error(' laneBarcode.html cannot be found at {}'.format(path))

    def parse(self):
        self.sample_data = []
        self.flowcell_data = {}
        with open(self.path, newline='') as htmlfile:
            bsoup = BeautifulSoup(htmlfile, 'html.parser')
            flowcell_table = bsoup.find_all('table')[1]
            lane_table = bsoup.find_all('table')[2]

            keys = []
            values = []
            for th in flowcell_table.find_all('th'):
                keys.append(th.text)
            for td in flowcell_table.find_all('td'):
                values.append(td.text)

            self.flowcell_data = dict(zip(keys, values))

            keys = []
            rows = lane_table.find_all('tr')
            for row in rows[0:]:
                if len(row.find_all('th')):
                    # This is the header row
                    for th in row.find_all('th'):
                        key = th.text.replace(
                            '<br/>', ' ').replace(
                                '&gt;', '>')
                        keys.append(key)
                elif len(row.find_all('td')):
                    values = []
                    for td in row.find_all('td'):
                        values.append(td.text)

                    d = dict(zip(keys, values))
                    self.sample_data.append(d)


class SampleSheetParser(object):
    """Parses  Samplesheets, with their fake csv format.
    Should be instancied with the samplesheet path as an argument.

    .header : a dict containing the info located under the [Header] section
    .settings : a dict containing the data from the [Settings] section
    .reads : a list of the values in the [Reads] section
    .data : a list of the values under the [Data] section
    .datafields : a list of field names for the data section"""
    def __init__(self, path):
        self.log = logging.getLogger(__name__)
        if os.path.exists(path):
            self.parse(path)
        else:
            raise os.error(' sample sheet cannot be found at {}'.format(path))

    def parse(self, path):
        flag = None
        header = {}
        reads = []
        settings = []
        csvlines = []
        data = []
        flag = 'data'  # In case of HiSeq samplesheet only data section is present
        separator = ','
        with open(path, newline='') as csvfile:
            # Ignore empty lines (for instance the Illumina Experiment Manager
            # generates sample sheets with empty lines
            lines = [nonempty_line for nonempty_line
                     in (line.rstrip() for line in csvfile) if nonempty_line]
            # Now parse the file
            for line in lines:
                if '[Header]' in line:
                    flag = 'HEADER'
                elif '[Reads]' in line:
                    flag = 'READS'
                elif '[Settings]' in line:
                    flag = 'SETTINGS'
                elif '[Data]' in line:
                    flag = 'data'
                else:
                    tokens = line.split(separator)
                    if flag == 'HEADER':
                        if len(tokens) < 2:
                            self.log.error('file {} does not have a correct format.'.format(path))
                            raise RuntimeError('Could not parse the samplesheet, the file does' \
                                               'not seem to have a correct format.')
                        header[tokens[0]] = tokens[1]
                    elif flag == 'READS':
                        reads.append(tokens[0])
                    elif flag == 'SETTINGS':
                        settings.append(tokens[0])
                    elif flag == 'data':
                        csvlines.append(line)
            reader = csv.DictReader(csvlines)
            for row in reader:
                linedict = {}
                for field in reader.fieldnames:
                    linedict[field] = row[field]
                data.append(linedict)

            self.datafields = reader.fieldnames
            self.dfield_sid = self._get_pattern_datafield(r'sample_?id')
            self.dfield_snm = self._get_pattern_datafield(r'sample_?name')
            self.dfield_proj = self._get_pattern_datafield(r'.*?project')
            self.data = data
            self.settings = settings
            self.header = header
            self.reads = reads

    def _get_pattern_datafield(self, pattern):
        for fld in self.datafields:
            if re.search(pattern, fld, re.IGNORECASE):
                return fld
        return ''


class RunInfoParser(object):
    """Parses  RunInfo.xml.
    Should be instancied with the file path as an argument.

    .data : a list of hand-picked values :
     -Run ID
     -Run Number
     -Instrument
     -Flowcell name
     -Run Date
     -Reads metadata
     -Flowcell layout
    """
    def __init__(self, path):
        self.data = {}
        self.recipe = None
        self.path = path
        if os.path.exists(path):
            self.parse()
        else:
            raise os.error(' run info cannot be found at {0}'.format(path))

    def parse(self):
        data = {}
        tree = ET.parse(self.path)
        root = tree.getroot()
        run = root.find('Run')
        data['Id'] = run.get('Id')
        data['Number'] = run.get('Number')
        data['Instrument'] = run.find('Instrument').text
        data['Flowcell'] = run.find('Flowcell').text

        # Change Novaseq date format from
        # 10/17/2017 10:59:16 AM to 171017 (yymmdd)
        if len(run.find('Date').text) > 6:
            data['Date'] = datetime.strptime(run.find('Date').text.split(' ')[0],
                                             '%m/%d/%Y').strftime('%y%m%d')
        else:
            data['Date'] = run.find('Date').text
        data['Reads'] = []
        for read in run.find('Reads').findall('Read'):
            data['Reads'].append(read.attrib)
        layout = run.find('FlowcellLayout')
        data['FlowcellLayout'] = layout.attrib
        self.data = data
        self.recipe = make_run_recipe(self.data.get('Reads', {}))

    def get_read_configuration(self):
        """Return a list of dicts containig the Read Configuration."""
        readConfig = []
        try:
            readConfig = self.data['Reads']
            return sorted(readConfig, key=lambda r: int(r.get('Number', 0)))
        except IOError:
            raise RuntimeError('Reads section not present in RunInfo.',
                               'Check the FC folder.')


class RunParametersParser(object):
    """Parses a runParameters.xml file.
    This is a much more general xml parser, it will build a dict from the
    xml data. Attributes might be replaced if children nodes have the same
    tag as the attributes. This does not happen in the current xml file,
    but if you're planning to reuse this, it may be of interest.
    """

    def __init__(self, path):
        self.data = {}
        self.recipe = None
        self.path = path
        if os.path.exists(path):
            self.parse()
        else:
            raise os.error('RunParameters file cannot be found at {0}'.format(path))

    def parse(self):
        tree = ET.parse(self.path)
        root = tree.getroot()
        self.data = xml_to_dict(root)
        self.recipe = make_run_recipe(self.data.get(
            'Setup', {}).get(
                'Reads', {}).get(
                    'Read', {}))


def make_run_recipe(reads):
    """Based on either runParameters of RunInfo,
    gathers the information as to how many
    readings are done and their length, e.g. 2x150"""
    nb_reads = 0
    nb_indexed_reads = 0
    numCycles = 0
    for read in reads:
        nb_reads += 1
        if read['IsIndexedRead'] == 'Y':
            nb_indexed_reads += 1
        else:
            if numCycles and numCycles != read['NumCycles']:
                logging.warn('NumCycles in not coherent')
            else:
                numCycles = read['NumCycles']

    if reads:
        return '{}x{}'.format(nb_reads - nb_indexed_reads, numCycles)
    return None


def xml_to_dict(root):
    current = None

    children = list(root)
    if children:
        current = {}
        duplicates = {}
        for child in children:
            if len(root.findall(child.tag)) > 1:
                if child.tag not in duplicates:
                    duplicates[child.tag] = []
                lower = xml_to_dict(child)
                duplicates[child.tag].extend(list(lower.values()))
                current.update(duplicates)
            else:
                lower = xml_to_dict(child)
                current.update(lower)
    if root.attrib:
        if current:
            if [x in current for x in root.attrib]:
                current.update(root.attrib)
            else:
                current.update({'attribs': root.attribs})
        else:
            current = root.attrib
    if root.text and root.text.strip() != '':
        if current:
            if 'text' not in current:
                current['text'] = root.text
            else:
                # you're really pushing here, pal
                current['xml_text'] = root.text
        else:
            current = root.text
    return {root.tag: current}


class CycleTimesParser(object):
    def __init__(self, path):
        if os.path.exists(path):
            self.path = path
            self.cycles = []
            self.parse()
        else:
            raise os.error('file {0} cannot be found'.format(path))

    def parse(self):
        """Parse CycleTimes.txt and return ordered list of cycles
        CycleTimes.txt contains records:
        <date> <time> <barcode> <cycle> <info>
        one cycle contains a few records (defined by <cycle>)
        parser goes over records and saves the first record of each cycle as
        start time and the last record of each cycle as end time
        """
        data = []
        date_format = '%m/%d/%Y-%H:%M:%S.%f'
        with open(self.path, 'r') as file:
            cycle_times = file.readlines()
            # If file is empty, return
            if not cycle_times:
                return

            # First line is header, don't read it
            for cycle_line in cycle_times[1:]:
                # Split line into strings
                cycle_list = cycle_line.split()
                cycle_time_obj = {}
                # Parse datetime
                cycle_time_obj['datetime'] = datetime.strptime(
                    '{date}-{time}'.format(
                        date=cycle_list[0],
                        time=cycle_list[1]),
                    date_format)
                # Parse cycle number
                cycle_time_obj['cycle'] = int(cycle_list[3])
                # Add object in the list
                data.append(cycle_time_obj)

        # Take the first record as current cycle
        current_cycle = {
            'cycle_number': data[0]['cycle'],
            'start': data[0]['datetime'],
            'end': data[0]['datetime']
        }
        # Compare each record with current cycle (except the first one)
        for record in data[1:]:
            # If we are at the same cycle
            if record['cycle'] == current_cycle['cycle_number']:
                # Override end of cycle with current record
                current_cycle['end'] = record['datetime']
            # If a new cycle starts
            else:
                # Save previous cycle
                self.cycles.append(current_cycle)
                # Initialize new current_cycle
                current_cycle = {
                    'cycle_number': record['cycle'],
                    'start': record['datetime'],
                    'end': record['datetime']
                }
        # The last records is not saved inside the loop
        if current_cycle not in self.cycles:
            self.cycles.append(current_cycle)


class StatsParser(object):

    def __init__(self, path):
        if os.path.exists(path):
            self.path = path
            self.cycles = []
            self.data = None
            self.parse()
        else:
            raise os.error('file {} cannot be found'.format(path))

    def parse(self):
        with open(self.path) as data:
            self.data = json.load(data)
