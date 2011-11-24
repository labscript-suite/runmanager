import sys,os
import socket
import urllib, urllib2

def submit_job(run_file,server):
    port = 42517
    # Workaround to force python not to use IPv6 for the request:
    address  = socket.gethostbyname(server)
    print 'Submitting run file %s.\n'%os.path.basename(run_file)
    params = urllib.urlencode({'filepath': os.path.abspath(run_file)})
    try:
        response = urllib2.urlopen('http://%s:%d'%(address,port), params, 2).read()
        if 'added successfully' in response:
            return response
        else:
            raise Exception(response)
    except Exception as e:
        raise Exception('Couldn\'t submit job to control server. Check network connectivity, and server address.\n%s'%str(e))
    
if __name__ == '__main__':
    if len(sys.argv) == 3:
        server = sys.argv[1]
        run_file = sys.argv[2]
    elif len(sys.argv) == 2:
        server = 'localhost'
        run_file = sys.argv[1]
        
    else:
        print 'Usage: python -m runviewer.submitjob [server-name] run_file.h5'
        sys.exit(1)
        
    print submit_job(run_file,server)
