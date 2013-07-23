from __future__ import print_function
from twisted.internet.protocol import DatagramProtocol
from twisted.internet import reactor
from twisted.internet import task
import sys
import argparse
import binascii
import hmac
import hashlib
import socket
#from logging.config import dictConfig
#import logging


#************************************************
#     IMPORTING OTHER FILES - '#include'
#************************************************

# Import system logger configuration
#
try:
    from ipsc_logger import logger
except ImportError:
    sys.exit('System logger configuraiton not found or invalid')

# Import configuration and informational data structures
#
try:
    from my_ipsc_config import NETWORK
except ImportError:
    sys.exit('Configuration file not found, or not valid formatting')

# Import IPSC message types and version information
#
try:
    from ipsc_message_types import *
except ImportError:
    sys.exit('IPSC message types file not found or invalid')

# Import IPSC flag mask values
#
try:
    from ipsc_mask import *
except ImportError:
    sys.exit('IPSC mask values file not found or invalid')
   


#************************************************
#     GLOBALLY SCOPED FUNCTIONS
#************************************************


# Take a packet to be SENT, calcualte auth hash and return the whole thing
#
def hashed_packet(_key, _data):
    hash = binascii.unhexlify((hmac.new(_key,_data,hashlib.sha1)).hexdigest()[:20])
    return (_data + hash)
    
    
# Take a RECEIVED packet, calculate the auth hash and verify authenticity
#
def validate_auth(_key, _data):
    return

# Decide the Mode bit flags and print them - later, use this for more
# than just informational purposes, for now, it's FYI/Debug info.
#
def print_mode_decode(_mode):
    _mode = int(binascii.b2a_hex(_mode), 16)
    link_op   = _mode & PEER_OP_MSK
    link_mode = _mode & PEER_MODE_MSK
    ts1       = _mode & IPSC_TS1_MSK
    ts2       = _mode & IPSC_TS2_MSK
    
    if link_op == 0b01000000:
        logger.debug('\t\tPeer Operational')
    elif link_op == 0b00000000:
        logger.debug('\t\tPeer Not Operational')
    else:
        logger.info('\t\tPeer Mode Invalid')
        
    if link_mode == 0b00000000:
        logger.debug('\t\tNo RF Interface')
    elif link_mode == 0b00010000:
        logger.debug('\t\tRadio in Analog Mode')
    elif link_mode == 0b00100000:
        logger.debug('\t\tRadio in Digital Mode')
    else:
        logger.info('\t\tRadio Mode Invalid')
        
    if ts1 == 0b00001000:
        logger.debug('\t\tIPSC Enabled on TS1')
    
    if ts2 == 0b00000010:
        logger.debug('\t\tIPSC Enabled on TS2')


# Gratuituous print-out of the peer list.. Pretty much debug stuff.
#
def print_peer_list(_network_name):
    logger.debug('\t%s', _network_name)
    for dictionary in NETWORK[_network_name]['PEERS']:    
        logger.debug('\tIP Address: %s:%s', dictionary['IP'], dictionary['PORT'])
        logger.debug('\tRADIO ID:   %s ', int(binascii.b2a_hex(dictionary['RADIO_ID']), 16))
        logger.debug('\tIPSC Mode:')
        print_mode_decode(dictionary['RAW_MODE'])
        logger.debug('\tConnection Status: %s', dictionary['STATUS']['CONNECTED'])
        logger.debug('\tKeepAlives Missed: %s', dictionary['STATUS']['KEEP_ALIVES_MISSED'])
        logger.debug('')
        


#************************************************
#********                             ***********
#********    IPSC Network 'Engine'    ***********
#********                             ***********
#************************************************

#************************************************
#     INITIAL SETUP of IPSC INSTANCE
#************************************************

class IPSC(DatagramProtocol):
    
    # Modify the initializer to set up our environment and build the packets
    # we need to maitain connections
    #
    def __init__(self, *args, **kwargs):
        if len(args) == 1:
            # Housekeeping: create references to the configuration and status data for this IPSC instance.
            #
            self._network_name = args[0]
            self._config = NETWORK[self._network_name]
            args = ()
            
            # Packet 'constructors' - builds the necessary control packets for this IPSC instance
            #
            self.TS_FLAGS             = (self._config['LOCAL']['MODE'] + self._config['LOCAL']['FLAGS'])
            self.MASTER_REG_REQ_PKT   = (MASTER_REG_REQ + self._config['LOCAL']['RADIO_ID'] + self.TS_FLAGS + IPSC_VER)
            self.MASTER_ALIVE_PKT     = (MASTER_ALIVE_REQ + self._config['LOCAL']['RADIO_ID'] + self.TS_FLAGS + IPSC_VER)
            self.PEER_LIST_REQ_PKT    = (PEER_LIST_REQ + self._config['LOCAL']['RADIO_ID'])
            self.PEER_REG_REQ_PKT     = (PEER_REG_REQ + self._config['LOCAL']['RADIO_ID'] + IPSC_VER)
            self.PEER_REG_REPLY_PKT   = (PEER_REG_REPLY + self._config['LOCAL']['RADIO_ID'] + IPSC_VER)
            self.PEER_ALIVE_REQ_PKT   = (PEER_ALIVE_REQ + self._config['LOCAL']['RADIO_ID'] + self.TS_FLAGS)
            self.PEER_ALIVE_REPLY_PKT = (PEER_ALIVE_REPLY + self._config['LOCAL']['RADIO_ID'] + self.TS_FLAGS)
            self._peer_list_new = False      
        else:
            # If we didn't get called correctly, log it!
            #
            logger.error('Unexpected arguments found.')
            
    # This is called by REACTOR when it starts, We use it to set up the timed
    # loop for each instance of the IPSC engine
    #       
    def startProtocol(self):
        # Timed loop for IPSC connection establishment and maintenance
        # Others could be added later for things like updating a Web
        # page, etc....
        #
        self._call = task.LoopingCall(self.timed_loop)
        self._loop = self._call.start(self._config['LOCAL']['ALIVE_TIMER'])


#************************************************
#     FUNCTIONS FOR IPSC Network Engine
#************************************************

    # Process a received peer list:
    #   Flag we have a list
    #   Flag the list is new (needed elsewhere)
    #   Populate the peer information from the list
    #
    def peer_list_received(self, _data, (_host, _port)):
        self._config['MASTER']['STATUS']['PEER-LIST'] = True
        self._peer_list_new = True
        logger.info('<<- The Peer List has been Received from Master:%s:%s ', _host, _port)
        _num_peers = int(str(int(binascii.b2a_hex(_data[5:7]), 16))[1:])
        self._config['LOCAL']['NUM_PEERS'] = _num_peers
        logger.debug('    There are %s peers in this IPSC Network', _num_peers)
        del self._config['PEERS'][:]
        for i in range(7, (_num_peers*11)+7, 11):
            hex_address = (_data[i+4:i+8])
            self._config['PEERS'].append({
                'RADIO_ID': _data[i:i+4], 
                'IP':       socket.inet_ntoa(hex_address), 
                'PORT':     int(binascii.b2a_hex(_data[i+8:i+10]), 16), 
                'RAW_MODE': _data[i+10:i+11],
                'STATUS':   {'CONNECTED': 0, 'KEEP_ALIVES_MISSED': 0}
            })
        print_peer_list(self._network_name)



#************************************************
#     TIMED LOOP - MY CONNECTION MAINTENANCE
#************************************************

    def timed_loop(self):
        logger.debug('timed loop started') # temporary debugging to make sure this part runs
        _master_connected = self._config['MASTER']['STATUS']['CONNECTED']
        _master_alives_missed = self._config['MASTER']['STATUS']['KEEP_ALIVES_MISSED']
        _peer_list_rx = self._config['MASTER']['STATUS']['PEER-LIST']

        if (_master_connected == False):
            reg_packet = hashed_packet(self._config['LOCAL']['AUTH_KEY'], self.MASTER_REG_REQ_PKT)
            self.transport.write(reg_packet, (self._config['MASTER']['IP'], self._config['MASTER']['PORT']))
            logger.info('->> Master Registration Request To:%s:%s From:%s', self._config['MASTER']['IP'], self._config['MASTER']['PORT'], binascii.b2a_hex(self._config['LOCAL']['RADIO_ID']))
        
        elif (_master_connected == True):
            master_alive_packet = hashed_packet(self._config['LOCAL']['AUTH_KEY'], self.MASTER_ALIVE_PKT)
            self.transport.write(master_alive_packet, (self._config['MASTER']['IP'], self._config['MASTER']['PORT']))
            logger.debug('->> Master Keep-alive Sent To:%s:%s', self._config['MASTER']['IP'], self._config['MASTER']['PORT'])
            self._config['MASTER']['STATUS']['KEEP_ALIVES_SENT'] += 1
            
            if (self._config['MASTER']['STATUS']['KEEP_ALIVES_OUTSTANDING']) > 0:
                self._config['MASTER']['STATUS']['KEEP_ALIVES_MISSED'] += 1
            
            if self._config['MASTER']['STATUS']['KEEP_ALIVES_OUTSTANDING'] >= self._config['LOCAL']['MAX_MISSED']:
                pass
                self._config['MASTER']['STATUS']['CONNECTED'] = False
                logger.error('Maximum Master Keep-Alives Missed -- De-registering the Master')
            
        else:
            logger.error('->> Master in UNKOWN STATE:%s:%s', self._config['MASTER']['IP'], self._config['MASTER']['PORT'])
                
        if  ((_master_connected == True) and (_peer_list_rx == False)):     
            peer_list_req_packet = hashed_packet(self._config['LOCAL']['AUTH_KEY'], self.PEER_LIST_REQ_PKT)
            self.transport.write(peer_list_req_packet, (self._config['MASTER']['IP'], self._config['MASTER']['PORT']))
            logger.info('->> List Reqested from Master:%s:%s', self._config['MASTER']['IP'], self._config['MASTER']['PORT'])

# Logic problems in the next if.... bad ones. Fix them.
        if (self._peer_list_new == True):
            self._peer_list_new = False
            logger.info('*** NEW PEER LIST RECEIEVED - PROCESSING!')
            for peer in (self._config['PEERS']):
                if (peer['RADIO_ID'] == self._config['LOCAL']['RADIO_ID']): # We are in the peer-list, but don't need to talk to ourselves
                    continue
                if peer['STATUS']['CONNECTED'] == 0:
                    peer_reg_packet = hashed_packet(self._config['LOCAL']['AUTH_KEY'], self.PEER_REG_REQ_PKT)
                    self.transport.write(peer_reg_packet, (peer['IP'], peer['PORT']))
                    logger.info('->> Peer Registration Request To:%s:%s From:%s', peer['IP'], peer['PORT'], binascii.b2a_hex(self._config['LOCAL']['RADIO_ID']))
                elif peer['STATUS']['CONNECTED'] == 1:
                    peer_alive_req_packet = hashed_packet(self._config['LOCAL']['AUTH_KEY'], self.PEER_ALIVE_REQ_PKT)
                    self.transport.write(peer_alive_req_packet, (peer['IP'], peer['PORT']))
                    logger.info('->> Peer Keep-Alive Request To:%s:%s From:%s', peer['IP'], peer['PORT'], binascii.b2a_hex(self._config['LOCAL']['RADIO_ID']))
        
        logger.debug('timed loop finished') # temporary debugging to make sure this part runs
    
    
    
#************************************************
#     RECEIVED DATAGRAM - ACT IMMEDIATELY!!!
#************************************************

    # Work in progress -- at the very least, notify we have the packet. Ultimately
    # call a function or process immediately if only a few actions
    #
    def datagramReceived(self, data, (host, port)):
        logger.debug('datagram received') # temporary debugging to make sure this part runs
        dest_ip = self._config['MASTER']['IP']
        dest_port = self._config['MASTER']['PORT']
        #logger.debug('received %r from %s:%d', binascii.b2a_hex(data), host, port)

        _packettype = data[0:1]
        _peerid     = data[1:5]

        if (_packettype == PEER_ALIVE_REQ):
            logger.debug('<<- Peer Keep-alive Request From Peer ID %s at:%s:%s', int(binascii.b2a_hex(_peerid), 16), host, port)
            peer_alive_reply_packet = hashed_packet(self._config['LOCAL']['AUTH_KEY'], self.PEER_ALIVE_REPLY_PKT)
            self.transport.write(peer_alive_reply_packet, (host, port))
            logger.info('->> Peer Keep-alive Reply sent To:%s:%s', host, port)

        elif (_packettype == MASTER_ALIVE_REPLY):
            logger.info('<<- Master Keep-alive Reply  From:%s:%s', host, port)

        elif (_packettype == PEER_ALIVE_REPLY):
            logger.debug('<<- Peer Keep-alive Reply From:%s:%s', host, port)
            
        elif (_packettype == MASTER_REG_REQ):
            logger.debug('<<- Registration Packet Recieved')

        elif (_packettype == MASTER_REG_REPLY):
            self._config['MASTER']['STATUS']['CONNECTED'] = True
            self._config['MASTER']['STATUS']['KEEP_ALIVES_OUTSTANDING'] = 0
            logger.info('<<- Master Registration Reply From:%s:%s ', host, port)

        elif (_packettype == PEER_REG_REQ):
            logger.debug('<<- Peer Registration Request From Peer ID %s at:%s:%s', int(binascii.b2a_hex(_peerid), 16), host, port)
            peer_reg_reply_packet = hashed_packet(self._config['LOCAL']['AUTH_KEY'], self.PEER_REG_REPLY_PKT)
            self.transport.write(peer_reg_reply_packet, (host, port))
            logger.info('->> Peer Registration Reply Sent To:%s:%s', host, port)

        elif (_packettype == PEER_REG_REPLY):
            logger.info('<<- Peer Registration Reply From: %s @ IP: %s', int(binascii.b2a_hex(_peerid), 16), host)
            

        elif (_packettype == XCMP_XNL):
            logger.debug('<<- XCMP_XNL From:%s:%s, but we did not indicate XCMP capable!', host, port)

        elif (_packettype == PEER_LIST_REPLY):
            self.peer_list_received(data, (host, port))
            
        elif (_packettype == GROUP_VOICE):
            logger.debug('<<- Group Voice Packet From:%s:%s', host, port)
            
        elif (_packettype == PVT_VOICE):
            logger.debug('<<-  Voice Packet From:%s:%s', host, port)
            
        elif (_packettype == GROUP_DATA):
            logger.debug('<<- Group Data Packet From:%s:%s', host, port)
            
        elif (_packettype == PVT_DATA):
            logger.debug('<<- Private Data Packet From From:%s:%s', host, port)
            
        elif (_packettype == RPT_WAKE_UP):
            logger.info('<<- Repeater Wake-Up Packet From:%s:%s', host, port)
            
        elif (_packettype == DE_REG_REQ):
            logger.info('<<- Peer De-Registration Request From:%s:%s', host, port)
            
        elif (_packettype == DE_REG_REPLY):
            logger.debug('<<- Peer De-Registration Reply From:%s:%s', host, port)
            
        elif (_packettype in (CALL_CTL_1, CALL_CTL_2, CALL_CTL_3)):
            logger.debug('<<- Call Control Packet From:%s:%s', host, port)
            
        else:
            packet_type = binascii.b2a_hex(_packettype)
            logger.error('<<- Received Unprocessed Type %s From:%s:%s', packet_type, host, port)



#************************************************
#      MAIN PROGRAM LOOP STARTS HERE
#************************************************

if __name__ == '__main__':
    logger.debug('SYSTEM STARTING UP')
    for ipsc_network in NETWORK:
        reactor.listenUDP(NETWORK[ipsc_network]['LOCAL']['PORT'], IPSC(ipsc_network))
    reactor.run()