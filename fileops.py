import gtk
import h5py

class FileOps:
    def __init__(self, runmanager):
        self.runmanager = runmanager
        
    def handle_error(self,e):
        if '-debug' in sys.argv:
            raise e
        else:
            print str(e)
            md = gtk.MessageDialog(self.runmanager.window, gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_ERROR, 
                                   buttons =(gtk.BUTTONS_OK), message_format = str(e))
            md.run()
            md.destroy()
    
    def make_single_run_file(self, filename, sequenceglobals, runglobals, sequence_id, run_no, n_runs):
        with h5py.File(filename,'w') as f:
            f.attrs['sequence_id'] = sequence_id
            f.attrs['run number'] = run_no
            f.attrs['n_runs'] = n_runs
            f.create_group('globals')
            for groupname, groupvars in sequenceglobals.items():
                group = f['globals'].create_group(groupname)
                unitsgroup = group.create_group('units')
                for name, (value, units) in groupvars.items():
                    group.attrs[name] = value
                    unitsgroup.attrs[name] = units
            for name, value in runglobals.items():
                f['globals'].attrs[name] = value
    
                    
    def new_file(self,filename):
        try:
            with h5py.File(filename,'w') as f:
                f.create_group('globals')
            return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def get_grouplist(self,filename):
        try:
            with h5py.File(filename,'r') as f:
                grouplist = f['globals']
                # File closes after this function call, so have to
                # convert the grouplist generator to a list of strings
                # before its file gets dereferenced:
                return list(grouplist), True
        except Exception as e:
            self.handle_error(e)
            return [], False
        
    def new_group(self,filename,groupname):
        try:
            with h5py.File(filename,'a') as f:
                group = f['globals'].create_group(groupname)
                units = group.create_group('units')
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def rename_group(self,filename,oldgroupname,newgroupname):
        if oldgroupname == newgroupname:
            # No rename!
            return True
        try:
            with h5py.File(filename,'a') as f:
                f.copy(f['globals'][oldgroupname], '/globals/%s'%newgroupname)
                del f['globals'][oldgroupname]
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def delete_group(self,filename,groupname):
        try:
            with h5py.File(filename,'a') as f:
                del f['globals'][groupname]
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def get_globalslist(self,filename,groupname):
        try:
            with h5py.File(filename,'r') as f:
                group = f['globals'][groupname]
                # File closes after this function call, so have to convert
                # the attrs to a dict before its file gets dereferenced:
                return dict(group.attrs), True
        except Exception as e:
            self.handle_error(e)
            return {}, False
    
    def new_global(self,filename,groupname,globalname):
        try:
            with h5py.File(filename,'a') as f:
                group = f['globals'][groupname]
                if globalname in group.attrs:
                    raise Exception('Can\'t create global: target name already exists.')
                group.attrs[globalname] = ''
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def rename_global(self,filename,groupname,oldglobalname,newglobalname):
        if oldglobalname == newglobalname:
            # No rename!
            return True
        try:
            value, success = self.get_value(filename, groupname, oldglobalname)
            if success:
                units, success = self.get_units(filename, groupname, oldglobalname)
            if not success:
                return False
            with h5py.File(filename,'a') as f:
                group = f['globals'][groupname]
                if newglobalname in group.attrs:
                    raise Exception('Can\'t rename: target name already exists.')
                group.attrs[newglobalname] = value
                group['units'].attrs[newglobalname] = units
                del group.attrs[oldglobalname]
                del group['units'].attrs[oldglobalname]
                return True
        except Exception as e:
            self.handle_error(e)
            return False

    def get_value(self,filename,groupname,globalname):
        try:
            with h5py.File(filename,'r') as f:
                value = f['globals'][groupname].attrs[globalname]
                return value, True
        except Exception as e:
            self.handle_error(e)
            return None, False
                
    def set_value(self,filename,groupname,globalname, value):
        try:
            with h5py.File(filename,'a') as f:
                f['globals'][groupname].attrs[globalname] = value
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def get_units(self,filename,groupname,globalname):
        try:
            with h5py.File(filename,'r') as f:
                value = f['globals'][groupname]['units'].attrs[globalname]
                return value, True
        except Exception as e:
            self.handle_error(e)
            return None, False
    
    def set_units(self,filename,groupname,globalname, units):
        try:
            with h5py.File(filename,'a') as f:
                f['globals'][groupname]['units'].attrs[globalname] = units
                return True
        except Exception as e:
            self.handle_error(e)
            return False
    
    def delete_global(self,filename,groupname,globalname):
        try:
            with h5py.File(filename,'a') as f:
                group = f['globals'][groupname]
                del group.attrs[globalname]
                return True
        except Exception as e:
            self.handle_error(e)
            return False