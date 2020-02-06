"""A smart contract that implements a hierarchical distributed energy transaction platform."""

####################################################################
# imports
####################################################################
# general
from hashlib import sha256
from json    import dumps, loads
import os
import time, ast
import traceback
import requests
# chainspace
from chainspacecontract import ChainspaceContract
from chainspaceapi import ChainspaceClient
# crypto
from petlib.ecdsa import do_ecdsa_sign, do_ecdsa_verify
from chainspacecontract.examples.utils import setup, key_gen, pack, unpack

## contract name
contract = ChainspaceContract('surge')


## Class definitions for clients
DELTA = 5
class SurgeClient:
        
    def __init__(self, port, init_token=None):
        (self.priv, self.pub) = key_gen(setup())
        self.cs_client = ChainspaceClient(port=5000)
        if init_token:
            self.create_surge_client(init_token)
        
    def create_surge_client(self,token):
        if type(token) is not tuple:
            token = (token,)
        create_surge_client_txn = create_surge_client(
            token,
            None,
            (pack(self.pub),),
            pack(self.priv),
        )
        self.surge_client = create_surge_client_txn['transaction']['outputs'][0]
        self.vote_slip = create_surge_client_txn['transaction']['outputs'][1]
        self.ebtoken = create_surge_client_txn['transaction']['outputs'][2]
        self.cs_client.process_transaction(create_surge_client_txn)
    
    def cast_csc_vote(self, client_pub):
        cast_csc_vote_txn = cast_csc_vote(
            (self.vote_slip,),
            None,
            None,
            pack(self.priv),
            pack(client_pub),
        )
        vote_token = cast_csc_vote_txn['transaction']['outputs'][0]
        self.vote_slip = cast_csc_vote_txn['transaction']['outputs'][1]
        self.cs_client.process_transaction(cast_csc_vote_txn)
        return vote_token

    def cast_srep_vote(self, rep_pub):
        cast_srep_vote_txn = cast_srep_vote(
            (self.vote_slip,),
            None,
            None,
            pack(self.priv),
            pack(rep_pub),
        )
        vote_token = cast_srep_vote_txn['transaction']['outputs'][0]
        self.vote_slip = cast_srep_vote_txn['transaction']['outputs'][1]
        self.cs_client.process_transaction(cast_srep_vote_txn)
        return vote_token
    
    def submit_bid(self, bid_type, quantity):
        bid_proof_txn = submit_bid_proof(
            (self.ebtoken,),
            None,
            (bid_type,),
            pack(self.priv),
            quantity
        )
        bid_proof = bid_proof_txn['transaction']['outputs'][0]
        self.ebtoken = bid_proof_txn['transaction']['outputs'][1]
        self.cs_client.process_transaction(bid_proof_txn)
        # wait for others to submit their bid proofs
        # time.sleep(2*DELTA)
        
        # bid_txn = submit_bid(
        #     (bid_proof,),
        #     None,
        #     (quantity,),
        #     pack(self.priv)
        # )
        # bid = bid_txn['transaction']['outputs'][0]
        # self.cs_client.process_transaction(bid_txn)
        # return bid
        
class SREPClient (SurgeClient):
    
    def create_srep_client(self, srep_port, vote_tokens):
        self.srep_cs_client = ChainspaceClient(port=srep_port)
        create_srep_client_txn = create_srep_client(
            vote_tokens,
            None,
            (pack(self.pub),),
            pack(self.priv),
        )
        self.vote_slip = create_srep_client_txn['transaction']['outputs'][1]
        self.srep_client = create_srep_client_txn['transaction']['outputs'][0]
        self.srep_cs_client.process_transaction(create_srep_client_txn)
        
    def accept_bids(self):
        time.sleep(DELTA)
        bid_proofs = self.srep_cs_client.get_objects({'location':loads(self.srep_client)['location'], 'type':'BidProof'})
        bidders = {}
        for bid in bid_proofs:
            bid = loads(bid)
            bidders[str(bid['quantity_sig'])]= True
        
        time.sleep(2*DELTA)
        buy_bids = self.srep_cs_client.get_objects({'location':loads(self.srep_client)['location'], 'type':'EBBuy'})
        sell_bids = self.srep_cs_client.get_objects({'location':loads(self.srep_client)['location'], 'type':'EBSell'})
        # process bid
        accepted_bids = []
        for bid in buy_bids:
            if not bidders.has_key(loads(bid)['quantity_sig']):
                continue
            accepted_bids.append(bid)
        for bid in sell_bids:
            if not bidders.has_key(loads(bid)['quantity_sig']):
                continue
            accepted_bids.append(bid)
        if len(accepted_bids)==0:
            return None
        accept_bids_txn = accept_bids(
            tuple(accepted_bids),
            None,
            (pack(self.pub),),
            pack(self.priv),
        )
        bid_accept = accept_bids_txn['transaction']['outputs'][0]
        self.srep_cs_client.process_transaction(accept_bids_txn)
        return bid_accept
        
## Helper functions
def pb():
    print "**********************  BEGIN  ******************************"
def pe():
    print "***********************  END  *******************************"

def validate(object, keys):
    for key in keys:
        if not object.has_key(key):
            raise Exception("Invalid object format")
        if object[key] == None:
            raise Exception("Invalid object format")

def check_type(object, T):
    if not object['type'] == T:
        raise Exception("Invalid object type")

def equate(a, b):
    if a!=b:
        raise Exception(str(a) + "not equal to " + str(b))

def generate_sig(priv, msg = "proof"):
    hasher = sha256()
    hasher.update(msg)

    # sign message
    G = setup()[0]
    sig = do_ecdsa_sign(G, unpack(priv), hasher.digest())
    return pack(sig)

def validate_sig(sig, pub, msg="proof"):
    # check that the signature on the proof is correct
    hasher = sha256()
    hasher.update(msg)
    # verify signature
    (G, _, _, _) = setup()
    if not do_ecdsa_verify(G, unpack(pub), unpack(sig), hasher.digest()):
        raise Exception("Invalid signature")

####################################################################
# methods and checkers
####################################################################
# ------------------------------------------------------------------
# init
# ------------------------------------------------------------------
@contract.method('init')
def init():
    r = requests.get('http://10.129.6.52:4999/setup.in')
    setup_str = r.text
    setup_str = setup_str.split('\n')
    NUM_SHARDS = int(setup_str[0])
    NUM_REPLICAS = int(setup_str[1])
    NUM_CLIENTS = int(setup_str[2])
        
    init_tokens = []
    for l in range(0,NUM_SHARDS):
        for i in range(0,NUM_CLIENTS):
            init_tokens.append(dumps({'type' : 'InitToken', 'location':l}))
    init_tokens = tuple(init_tokens)
    # return
    return {
        'outputs': init_tokens,
    }

# ------------------------------------------------------------------
# create surge client
# NOTE:
#   - only 'inputs', 'reference_inputs' and 'parameters' are used to the framework
#   - if there are more than 3 param, the checker has to be implemented by hand
# ------------------------------------------------------------------
@contract.method('create_surge_client')
def create_surge_client(inputs, reference_inputs, parameters, priv):

    pub = parameters[0]
    # new client
    new_surge_client = {
        'type'           : 'SurgeClient', 
        'pub'            : pub, 
        'location'       : loads(inputs[0])['location']
    }
    vote_slip = {
        'type':'VoteSlipToken',
        'pub':pub,
        'location':loads(inputs[0])['location']
    }
    ebtoken = {
        'type':'EBToken',
        'pub':pub,
        'location':loads(inputs[0])['location']
    }
    # return
    return {
        'outputs': ( dumps(new_surge_client), dumps(vote_slip), dumps(ebtoken)),
        'extra_parameters': (
            generate_sig(priv),
        )
    }

# ------------------------------------------------------------------
# check create_surge_client
# ------------------------------------------------------------------
@contract.checker('create_surge_client')
def create_surge_client_checker(inputs, reference_inputs, parameters, outputs, returns, dependencies):
    try:
        REQUIRED_VOTES=2 # set to the number of CSCVoteTokens required to be accepted as a client

        # loads data
        surge_client = loads(outputs[0])
        vote_slip = loads(outputs[1])
        ebtoken = loads(outputs[2])
        
        # check argument lengths
        if len(inputs) < 1 or len(reference_inputs) != 0 or len(parameters)!=2 or len(outputs) != 3 or len(returns) != 0:
            raise Exception("Invalid argument lengths")
        # key validations
        validate(surge_client, ['type','pub','location'])
        validate(vote_slip, ['type','pub','location'])
        validate(ebtoken, ['type','pub','location'])
        
        # type checks
        # Since input can be InitToken or CSCVoteToken we cannot check type here
        check_type(surge_client, 'SurgeClient')
        check_type(vote_slip, 'VoteSlipToken')
        check_type(ebtoken, 'EBToken')
        # explicit type checks
        if not(loads(inputs[0])['type'] == 'InitToken' or loads(inputs[0])['type'] == 'CSCVoteToken'):
            raise Exception("Invalid input token types")
        # equality checks
        equate(surge_client['pub'], parameters[0])
        equate(surge_client['pub'], vote_slip['pub'])
        equate(surge_client['pub'], ebtoken['pub'])
        equate(surge_client['location'], vote_slip['location'])
        equate(surge_client['location'], loads(inputs[0])['location'])
        equate(surge_client['location'], ebtoken['location'])
        
        # signature validation
        validate_sig(parameters[1], parameters[0])

        # contract logic
        if loads(inputs[0])['type'] == 'InitToken' :
            return True
        # validate CSC votes if InitToken is not provided
        voters = {}
        for vote in inputs:
            vote = loads(vote)
            check_type(vote, 'CSCVoteToken')
            equate(vote['granted_to'], parameters[0])
            voters[str(vote['granted_by'])]= True
        
        if len(voters) < REQUIRED_VOTES:
            raise Exception("Not enough voters")

    except Exception as e:
        print e
        return False    
    
    return True


# ------------------------------------------------------------------
# cast csc (create surge client) vote
# NOTE:
#   - cast a vote to allow a new client to be added to the platform
#   - only 'inputs', 'reference_inputs' and 'parameters' are used to the framework
#   - if there are more than 3 param, the checker has to be implemented by hand 
#   - inputs must contain a valid VoteSlipToken
#   - parameters contain a proof signed by the caster's private key
#   - the SurgeClient object will be used to validate the signature
# ------------------------------------------------------------------
@contract.method('cast_csc_vote')
def cast_csc_vote(inputs, reference_inputs, parameters, surge_client_priv, granted_to_pub):

    # vote_slip = inputs[0]
    # proof = parameters[0]
    granted_by_pub = loads(inputs[0])['pub']

    vote_token = {
        'type'          : 'CSCVoteToken', 
        'granted_by'    : granted_by_pub,
        'granted_to'    : granted_to_pub,
        'location'      : loads(inputs[0])['location']
    }

    return {
        'outputs': ( dumps(vote_token), inputs[0]),
        'extra_parameters': (
            generate_sig(surge_client_priv),
        )
    }
    
# ------------------------------------------------------------------
# check cast_csc_vote
# ------------------------------------------------------------------
@contract.checker('cast_csc_vote')
def cast_csc_vote_checker(inputs, reference_inputs, parameters, outputs, returns, dependencies):
    try:

        # loads data
        vote_slip = loads(inputs[0])
        vote_token = loads(outputs[0])
        new_vote_slip = loads(outputs[1])
        
        # check argument lengths
        if len(inputs) != 1 or len(reference_inputs) != 0 or len(parameters)!=1 or len(outputs) != 2 or len(returns) != 0:
            raise Exception("Invalid argument lengths")
        # key validations
        validate(vote_token, ['type','granted_by', 'granted_to','location'])
        validate(new_vote_slip, ['type','pub','location'])
        # type checks
        check_type(vote_slip, 'VoteSlipToken')
        check_type(vote_token, 'CSCVoteToken')
        check_type(new_vote_slip, 'VoteSlipToken')
        # equality checks
        equate(vote_slip['pub'], new_vote_slip['pub'])
        equate(vote_slip['location'], new_vote_slip['location'])
        equate(vote_token['granted_by'], vote_slip['pub'])
        equate(vote_token['location'], vote_slip['location'])        
        # signature validation
        validate_sig(parameters[0], vote_slip['pub'])
        
    except Exception as e:
        print e
        return False
    return True



# ------------------------------------------------------------------
# cast srep (shard representative) vote 
# NOTE:
#   - cast a vote to allow a client to act as shard representative
#   - inputs must contain a valid VoteSlipToken
#   - parameters contain a proof signed by the caster's private key
# ------------------------------------------------------------------
@contract.method('cast_srep_vote')
def cast_srep_vote(inputs, reference_inputs, parameters, priv, granted_to_pub):

    # vote_slip = inputs[0]
    # proof = parameters[0]
    granted_by_pub = loads(inputs[0])['pub']

    vote_token = {
        'type'          : 'SREPVoteToken', 
        'granted_by'    : granted_by_pub,
        'granted_to'    : granted_to_pub,
        'location'      : loads(inputs[0])['location']
    }

    return {
        'outputs': ( dumps(vote_token), inputs[0]),
        'extra_parameters': (
            generate_sig(priv),
        )
    }
    
# ------------------------------------------------------------------
# check cast_srep_vote
# ------------------------------------------------------------------
@contract.checker('cast_srep_vote')
def cast_srep_vote_checker(inputs, reference_inputs, parameters, outputs, returns, dependencies):
    try:

        # loads data
        vote_slip = loads(inputs[0])
        vote_token = loads(outputs[0])
        new_vote_slip = loads(outputs[1])
        
        # check argument lengths
        if len(inputs) != 1 or len(reference_inputs) != 0 or len(parameters)!=1 or len(outputs) != 2 or len(returns) != 0:
            raise Exception("Invalid argument lengths")
        # key validations
        validate(vote_token, ['type','granted_by', 'granted_to','location'])
        validate(new_vote_slip, ['type','pub','location'])
        # type checks
        check_type(vote_slip, 'VoteSlipToken')
        check_type(vote_token, 'SREPVoteToken')
        check_type(new_vote_slip, 'VoteSlipToken')
        # equality checks
        equate(vote_slip['pub'], new_vote_slip['pub'])
        equate(vote_slip['location'], new_vote_slip['location'])
        equate(vote_token['granted_by'], vote_slip['pub'])
        equate(vote_token['location'], vote_slip['location'])        
        # signature validation
        validate_sig(parameters[0], vote_slip['pub'])
        
    except Exception as e:
        print e
        return False
    return True


# ------------------------------------------------------------------
# create srep client
# NOTE:
#   - only 'inputs', 'reference_inputs' and 'parameters' are used to the framework
#   - if there are more than 3 param, the checker has to be implemented by hand
# ------------------------------------------------------------------
@contract.method('create_srep_client')
def create_srep_client(inputs, reference_inputs, parameters, priv):

    pub = parameters[0]
    # new client
    srep_client = {
        'type'           : 'SREPClient', 
        'pub'            : pub, 
        'location'       : loads(inputs[0])['location']
    }
    vote_slip = {
        'type':'VoteSlipToken',
        'pub':pub,
        'location':loads(inputs[0])['location']
    }
    # return
    return {
        'outputs': ( dumps(srep_client), dumps(vote_slip)),
        'extra_parameters': (
            generate_sig(priv),
        )
    }

# ------------------------------------------------------------------
# check create_srep_client
# ------------------------------------------------------------------
@contract.checker('create_srep_client')
def create_srep_client_checker(inputs, reference_inputs, parameters, outputs, returns, dependencies):
    try:
        REQUIRED_VOTES=2 # set to the number of CSCVoteTokens required to be accepted as a client

        # loads data
        srep_client = loads(outputs[0])
        vote_slip = loads(outputs[1])
        srep_vote_1 = loads(inputs[0])
        
        # check argument lengths
        if len(reference_inputs) != 0 or len(parameters)!=2 or len(outputs) != 2 or len(returns) != 0:
            raise Exception("Invalid argument lengths")
        # key validations
        validate(srep_client, ['type','pub','location'])
        validate(vote_slip, ['type','pub','location'])
        validate(srep_vote_1, ['type','granted_by', 'granted_to','location'])
        
        # type checks
        check_type(srep_client, 'SREPClient')
        check_type(vote_slip, 'VoteSlipToken')
        check_type(srep_vote_1, 'SREPVoteToken')
        # equality checks
        equate(srep_client['pub'], parameters[0])
        equate(srep_client['pub'], vote_slip['pub'])
        equate(srep_client['location'], vote_slip['location'])
        equate(srep_client['location'], srep_vote_1['location'])
        
        # signature validation
        validate_sig(parameters[1], parameters[0])

        # validate CSC votes if InitToken is not provided
        voters = {}
        for vote in inputs:
            vote = loads(vote)
            check_type(vote, 'SREPVoteToken')
            equate(vote['granted_to'], parameters[0])
            equate(vote['location'], srep_client['location'])
            voters[str(vote['granted_by'])]= True
        
        if len(voters) < REQUIRED_VOTES:
            raise Exception("Not enough voters")

    except Exception as e:
        print e
        return False    
    
    return True




# ------------------------------------------------------------------
# submit bid proof
# NOTE:
#   - before making a bit each client must submit a bid hash to prove the bid value
#   - inputs must contain a valid EBToken
#   - parameters must contain the bid type
#   - outputs must contain a valid BidProof and another EBToken
#   - client's private key to be provided as extra argument to be used for signature
#   - bid quantity to be provided as extra argument 
# ------------------------------------------------------------------
@contract.method('submit_bid_proof')
def submit_bid_proof(inputs, reference_inputs, parameters, priv, quantity):
    ebtoken = loads(inputs[0])
    bid_proof = {
        'type':'BidProof',
        'bid_type' : parameters[0],
        'quantity_sig' : generate_sig(priv, '{}|{}'.format(quantity, ebtoken['pub'])),
        'pub':ebtoken['pub'],
        'location' : ebtoken['location']
    }
    return {
        'outputs' : (dumps(bid_proof), dumps(ebtoken)),
        'extra_parameters': (generate_sig(priv),)
    }
# ------------------------------------------------------------------
# check submit_bid_proof
# ------------------------------------------------------------------
@contract.checker('submit_bid_proof')
def submit_bid_proof_checker(inputs, reference_inputs, parameters, outputs, returns, dependencies):
    try:

        # loads data
        old_ebtoken = loads(inputs[0])
        bid_proof = loads(outputs[0])
        new_ebtoken = loads(outputs[1])
        
        # check argument lengths
        if len(inputs) != 1 or len(reference_inputs) != 0 or len(parameters)!=2 or len(outputs) != 2 or len(returns) != 0:
            raise Exception("Invalid argument lengths")
        # key validations
        validate(old_ebtoken, ['type','pub','location'])
        validate(bid_proof, ['type', 'bid_type', 'quantity_sig', 'pub', 'location'])
        validate(new_ebtoken, ['type','pub','location'])
        # type checks
        check_type(old_ebtoken, 'EBToken')
        check_type(new_ebtoken, 'EBToken')
        check_type(bid_proof, 'BidProof')
        # equality checks
        equate(old_ebtoken['pub'], bid_proof['pub'])
        equate(old_ebtoken['pub'], new_ebtoken['pub'])

        equate(old_ebtoken['location'], bid_proof['location'])
        equate(old_ebtoken['location'], new_ebtoken['location'])        
        # signature validation
        validate_sig(parameters[1], old_ebtoken['pub'])
        
    except Exception as e:
        print e
        return False
    return True


# ------------------------------------------------------------------
# submit bid
# NOTE:
#   - make a bid for buying or selling some fixed unit of energy for the next time slot
#   - inputs must contain a valid BidProof object
#   - outputs must contain a valid EBBuy or EBSell object
#   - client's private key to be provided as extra argument to be used for signature
#   - 
# ------------------------------------------------------------------
@contract.method('submit_bid')
def submit_bid(inputs, reference_inputs, parameters, priv):
    bid_proof = loads(inputs[0])
    bid = {
        'type' : bid_proof['bid_type'],
        'quantity' : parameters[0],
        'quantity_sig':bid_proof['quantity_sig'],
        'pub':bid_proof['pub'],
        'location' : bid_proof['location']
    }
    return {
        'outputs' : (dumps(bid),),
        'extra_parameters': (generate_sig(priv),)
    }
# ------------------------------------------------------------------
# check submit_bid
# ------------------------------------------------------------------
@contract.checker('submit_bid')
def submit_bid_checker(inputs, reference_inputs, parameters, outputs, returns, dependencies):
    try:

        # loads data
        bid_proof = loads(inputs[0])
        bid = loads(outputs[0])
        
        # check argument lengths
        if len(inputs) != 1 or len(reference_inputs) != 0 or len(parameters)!=2 or len(outputs) != 1 or len(returns) != 0:
            raise Exception("Invalid argument lengths")
        # key validations
        validate(bid_proof, ['type', 'bid_type', 'quantity_sig','pub','location'])
        validate(bid, ['type', 'quantity', 'quantity_sig', 'pub', 'location'])
        # type checks
        check_type(bid_proof, 'BidProof')
        if not (bid['type'] == 'EBBuy' or bid['type'] == 'EBSell'):
            raise Exception("Invalid bid type")
        # equality checks
        equate(bid_proof['pub'], bid['pub'])
        equate(bid_proof['quantity_sig'], bid['quantity_sig'])

        equate(bid_proof['location'], bid['location'])     
        # signature validation
        validate_sig(parameters[1], bid_proof['pub'])

        # quantity signature validation
        validate_sig(bid_proof['quantity_sig'], bid_proof['pub'], '{}|{}'.format(bid['quantity'], bid['pub']))

        
    except Exception as e:
        print e
        return False
    return True



# ------------------------------------------------------------------
# accept bids
# NOTE:
#   - SREP executes this contract to accept bids and calculate diff
#   - inputs must contain list of EBBuy or EBSell objects
#   - outputs must contain a valid EBAccept object
#   - client's private key to be provided as extra argument to be used for signature
#   - 
# ------------------------------------------------------------------
@contract.method('accept_bids')
def accept_bids(inputs, reference_inputs, parameters, priv):
    total_buy = 0
    total_sell = 0
    for bid in inputs:
        b = loads(bid)
        if b['type'] == 'EBBuy':
            total_buy+= b['quantity']
        if b['type'] == 'EBSell':
            total_sell+= b['quantity']
    
    bid_accept = {
        'type' : 'BidAccept',
        'total_buy' : total_buy,
        'total_sell' : total_sell,
        'pub': parameters[0],
        'location' : loads(inputs[0])['location']
    }
    return {
        'outputs' : (dumps(bid_accept),),
        'extra_parameters': (generate_sig(priv),)
    }
# ------------------------------------------------------------------
# check accept_bids
# ------------------------------------------------------------------
@contract.checker('accept_bids')
def accept_bids_checker(inputs, reference_inputs, parameters, outputs, returns, dependencies):
    try:

        # loads data
        bid_accept = loads(outputs[0])
        
        # check argument lengths
        if len(inputs) < 1 or len(reference_inputs) != 0 or len(parameters)!=2 or len(outputs) != 1 or len(returns) != 0:
            raise Exception("Invalid argument lengths")
        # key validations
        validate(bid_accept, ['type', 'total_buy', 'total_sell','pub','location'])
        # type checks
        check_type(bid_accept, 'BidAccept')
        # equality checks
        total_buy = 0
        total_sell = 0
        for bid in inputs:
            b = loads(bid)
            equate(bid_accept['location'], b['location'])
            if b['type'] == 'EBBuy':
                total_buy+= b['quantity']
            if b['type'] == 'EBSell':
                total_sell+= b['quantity']

        equate(total_buy, bid_accept['total_buy'])
        equate(total_sell, bid_accept['total_sell'])
        # signature validation
        validate_sig(parameters[1], parameters[0])

        
    except Exception as e:
        traceback.print_exc()
        return False
    return True

####################################################################
# main
####################################################################
if __name__ == '__main__':
    contract.run()



####################################################################
